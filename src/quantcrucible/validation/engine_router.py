"""Route every sandbox job to the right backtest engine (P3-42, ADR-0038, ADR-0039).

:class:`EngineRouter` is a :class:`~quantcrucible.validation.sandbox.JobRunner`: the gates keep
submitting ``SandboxJob``s and learn which engine answered only through ``SandboxResult.engine``,
which the trial records (INV-108).

A ``backtest`` or ``grid_backtest`` job goes to the kernel engine when it is a spot
``bracket_timeout_v1`` job on one instrument whose source is the exact render of a genome
(``parse_genome``, INV-106) that compiles (INV-107). Everything else — leak checks, hand-written or
LLM strategies — runs in the Docker sandbox, except in a float32 campaign, which never falls back
to the float64 sandbox and refuses instead (no trial; ADR-0022, ADR-0039).

Audits (fail closed, INV-109):

- L0: before a process trusts CUDA, the device reproduces the CPU build on a fixture job.
- L1: a sampled share of CUDA jobs re-runs on the CPU build and must match byte for byte.
- L2 (float64): a sampled share, at ``operational.compute.audit_rate``, re-runs in the sandbox —
  the canonical engine; a gate-④ job re-runs its first and one other configuration.

A mismatch records ``ENGINE_AUDIT_MISMATCH`` naming the kernel version, refuses that engine for
good (read back at start-up) and answers the job with the canonical result.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np

from quantcrucible.config.schema import Compute
from quantcrucible.core.strategy import registry
from quantcrucible.core.strategy.base import FLAT, Bars, Signal
from quantcrucible.core.strategy.genome import feature_specs
from quantcrucible.core.strategy.genome_parse import GenomeParseError, ParsedGenome, parse_genome
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent
from quantcrucible.validation.backtest_report import (
    GridRow,
    backtest_report,
    grid_report,
    indicator_corr,
    signals_stream,
    timestamps,
)
from quantcrucible.validation.numerics import Numerics
from quantcrucible.validation.sandbox import JobRunner, SandboxJob, SandboxResult

KERNEL_KINDS = frozenset({"backtest", "grid_backtest"})
L1_RATE = 0.10  # share of CUDA jobs cross-checked against the CPU build
CUDA, CPU, SANDBOX = "cuda", "cpu_kernel", "sandbox"
GRID_AUDIT_KEYS = ("ts", "returns", "n_trades", "avg_holding_bars")


class Unsupported(Exception):
    """The job is outside what the kernel engine computes."""


@dataclass(frozen=True)
class _Plan:
    parsed: ParsedGenome
    bars: Bars
    configs: list[dict[str, Any]]
    lookback: int
    options: Mapping[str, Any]


def _kernels() -> Any:
    """The kernel package, imported on first use: numba is an optional dependency group."""
    from quantcrucible.execution.kernels import backend, program, version

    return backend, program, version


def _fraction(*parts: str) -> float:
    """A deterministic draw in [0, 1): the same job is always audited, or never."""
    h = hashlib.sha256("\0".join(parts).encode()).digest()
    return int.from_bytes(h[:8], "big") / 2**64


class EngineRouter:
    def __init__(
        self,
        sandbox: JobRunner,
        lock: Mapping[str, Any],
        compute: Compute,
        ledger_path: Path | None = None,
        run_label: str = "default",
    ) -> None:
        self.sandbox = sandbox
        self.lock = lock
        self.compute = compute
        self.numerics = Numerics.from_lock(lock)
        self.ledger_path = ledger_path
        self.run_label = run_label
        self._local = threading.local()  # a SQLite connection is bound to its thread
        self._cuda_lock = threading.Lock()  # one CUDA context, used by one thread at a time
        self._cuda_ok: bool | None = None  # None: not self-tested yet
        self._banned = self._read_bans()

    @property
    def fp32(self) -> bool:
        return self.numerics.precision == "float32"

    # ── plumbing ─────────────────────────────────────────────────────────────────────────────
    def kill_all(self) -> int:
        kill: Callable[[], int] | None = getattr(self.sandbox, "kill_all", None)
        return kill() if kill is not None else 0

    def _ledger(self) -> Ledger | None:
        if self.ledger_path is None:
            return None
        if getattr(self._local, "ledger", None) is None:
            self._local.ledger = Ledger.open(self.ledger_path)
        ledger: Ledger = self._local.ledger
        return ledger

    def _event(self, event: Event, detail: dict[str, Any]) -> None:
        ledger = self._ledger()
        if ledger is not None:
            ledger.log_event(
                GenerationEvent(
                    run_id=self.run_label, campaign_id=str(self.lock.get("campaign_id", "")),
                    engine="engine_router", seed=0, agent="engine_router", model_used="none",
                    event=event, detail={**detail, "kernel_version": self.version},
                )
            )  # fmt: skip

    @property
    def version(self) -> str:
        try:
            return str(_kernels()[2].KERNEL_VERSION)
        except ImportError:
            return "unavailable"

    def _read_bans(self) -> set[str]:
        ledger = self._ledger()
        if ledger is None:
            return set()
        return {
            str(d.get("engine"))
            for d in ledger.events_named(Event.ENGINE_AUDIT_MISMATCH)
            if d.get("kernel_version") == self.version
        }

    @staticmethod
    def _refuse(reason: str) -> SandboxResult:
        report = {"ok": False, "error": f"kernel engine: {reason}"}
        return SandboxResult(False, report, "", "", None, False, None, 0.0, engine="refused")

    # ── routing ──────────────────────────────────────────────────────────────────────────────
    def run(self, job: SandboxJob) -> SandboxResult:
        if job.kind not in KERNEL_KINDS:
            if self.fp32 and job.kind != "leak_check":  # leak checks price nothing
                return self._refuse(f"a float32 campaign has no kernel for {job.kind!r} jobs")
            return self.sandbox.run(job)
        if self.compute.engine == SANDBOX:  # the loader refuses sandbox + float32
            return self.sandbox.run(job)
        try:
            plan = self._plan(job)
        except Unsupported as exc:
            return self._refuse(str(exc)) if self.fp32 else self.sandbox.run(job)
        engine = self._choose()
        if engine is None:
            return (
                self._refuse("every kernel build is refused")
                if self.fp32
                else self.sandbox.run(job)
            )
        started = time.perf_counter()
        try:
            result = self._compute(plan, job.kind, engine)
        except Exception as exc:  # a device fault, an overflowed log, a numba error
            reason = f"{type(exc).__name__}: {exc}"
            self._event(
                Event.ENGINE_FALLBACK, {"engine": engine, "kind": job.kind, "reason": reason}
            )
            if engine == CUDA:
                self._cuda_ok = False  # a lost context is not recoverable in-process
                return self.run(job)
            return self._refuse(reason) if self.fp32 else self.sandbox.run(job)
        audit = []
        if engine == CUDA and _fraction("L1", self.version, self._key(job)) < L1_RATE:
            audit.append("L1")
            if _canon(result) != _canon(self._compute(plan, job.kind, CPU)):
                return self._mismatch(job, engine, "L1: CUDA differs from the CPU build")
        if (
            not self.fp32
            and _fraction("L2", self.version, self._key(job)) < self.compute.audit_rate
        ):
            audit.append("L2")
            canonical = self._sandbox_audit(job, plan, result, engine)
            if canonical is not None:
                return canonical
        report = {
            "ok": True, "kind": job.kind, "result": result, "engine": engine,
            "kernel_version": self.version, "numerics": self.numerics.tag,
            "audit": "+".join(audit) or "none",
        }  # fmt: skip
        elapsed = time.perf_counter() - started
        return SandboxResult(
            True, report, "", "", 0, False, None, elapsed, f"kernel:{engine}", engine=engine
        )

    def _choose(self) -> str | None:
        """The engine for a kernel job, or ``None`` when every build is refused."""
        cpu = None if CPU in self._banned else CPU
        if self.compute.engine == CPU:
            return cpu
        if CUDA not in self._banned and self._cuda_healthy():
            return CUDA
        return cpu

    def _cuda_healthy(self) -> bool:
        if self._cuda_ok is None:
            try:
                from quantcrucible.execution.kernels._numba import cuda_available

                with self._cuda_lock:
                    self._cuda_ok = cuda_available() and bool(
                        _kernels()[0].self_test(self.numerics.precision)
                    )
            except Exception:
                self._cuda_ok = False
            if not self._cuda_ok and self.compute.engine == "gpu":
                self._event(Event.ENGINE_FALLBACK, {"engine": CUDA, "reason": "self-test (L0)"})
        return bool(self._cuda_ok)

    @staticmethod
    def _key(job: SandboxJob) -> str:
        return json.dumps(
            [job.kind, job.source, dict(job.params), job.options.get("grid")],
            sort_keys=True, default=str,
        )  # fmt: skip

    # ── planning ─────────────────────────────────────────────────────────────────────────────
    def _plan(self, job: SandboxJob) -> _Plan:
        options = job.options
        policy = options.get("exit_policy") or {}
        if policy.get("mode") != "bracket_timeout_v1":
            raise Unsupported("only bracket_timeout_v1 jobs")
        if len(job.bars) != 1 or job.perp:
            raise Unsupported("only single-instrument spot jobs (perpetuals: P3-49..51)")
        ((symbol, bars),) = job.bars.items()
        if ":" in symbol:
            raise Unsupported("perpetual symbol")
        try:
            parsed = parse_genome(job.source)
        except GenomeParseError as exc:
            raise Unsupported(f"not a genome render: {exc}") from None
        if parsed.tp_sl_ratio is None or parsed.tp_sl_ratio != float(policy["tp_sl_ratio"]):
            raise Unsupported("the render's TP ratio differs from the locked one")
        if job.kind == "grid_backtest":
            configs = [dict(c) for c in options["grid"]]
        else:
            configs = [dict(job.params)]
        lookback = int(options["lookback"])
        _, program, _ = _kernels()
        try:
            program.compile_program(
                parsed.genome, parsed.direction, parsed.tp_sl_ratio, configs, lookback
            )
        except program.ProgramError as exc:
            raise Unsupported(f"does not compile: {exc}") from None
        if not np.isfinite(np.stack([bars.open, bars.high, bars.low, bars.close])).all():
            raise Unsupported("non-finite bars")
        return _Plan(parsed, bars, configs, lookback, options)

    # ── computing ────────────────────────────────────────────────────────────────────────────
    def _compute(self, plan: _Plan, kind: str, engine: str) -> dict[str, Any]:
        if engine == CUDA:
            with self._cuda_lock:
                return self._compute_on(plan, kind, "cuda")
        return self._compute_on(plan, kind, "cpu")

    def _compute_on(self, plan: _Plan, kind: str, target: str) -> dict[str, Any]:
        backend, program, _ = _kernels()
        from quantcrucible.execution.nautilus_bridge import CostModel, lot_step

        p, opts = plan.parsed, plan.options
        policy = opts["exit_policy"]
        spec = backend.ReplaySpec(
            fee=float(CostModel(**opts.get("costs", {})).taker_rate),
            lot_step=lot_step(plan.bars.symbol),
            max_risk_pct=float(opts["risk"]["max_risk_pct"]),
            initial_cash=float(opts.get("initial_cash", 100_000.0)),
            max_holding_bars=int(policy["max_holding_bars"]),
            tp_sl_ratio=float(policy["tp_sl_ratio"]),
        )
        prog = program.compile_program(
            p.genome, p.direction, p.tp_sl_ratio, plan.configs, plan.lookback
        )
        kb = backend.KernelBackend(target, self.numerics.precision)
        if kind == "grid_backtest":
            grid = kb.grid(prog, plan.bars, spec)
            rows = [
                GridRow(
                    grid.returns[m].tolist(), grid.n_trades[m], grid.avg_holding_bars[m], 0.0, None
                )
                for m in range(prog.n_configs)
            ]
            return _json(grid_report(timestamps(plan.bars.ts[1:]), rows))
        sig = kb.signals(prog, plan.bars)
        res = kb.backtest(prog, plan.bars, spec, sig)
        stream = signals_stream({plan.bars.symbol: _signals(sig, p)})
        corr, pair = indicator_corr([_features(p, plan.configs[0], plan.bars)])
        return _json(backtest_report(res, corr, pair, stream))

    # ── audits ───────────────────────────────────────────────────────────────────────────────
    def _sandbox_audit(
        self, job: SandboxJob, plan: _Plan, result: dict[str, Any], engine: str
    ) -> SandboxResult | None:
        """L2: the canonical engine on the same job (a gate-④ job: two of its configurations).
        ``None`` when it agrees; otherwise the canonical answer for the whole job."""
        picked = list(range(len(plan.configs)))
        probe = job
        if job.kind == "grid_backtest" and len(plan.configs) > 2:
            j = 1 + int(_fraction("L2-config", self._key(job)) * (len(plan.configs) - 1))
            picked = [0, j]
            probe = replace(job, options={**job.options, "grid": [plan.configs[i] for i in picked]})
        canon = self.sandbox.run(probe)
        if not canon.ok or canon.report is None:
            return None  # the canonical engine failed; that says nothing about the kernel
        want: dict[str, Any] = canon.report["result"]
        got = result
        if job.kind == "grid_backtest":
            got = {
                k: result[k] if k == "ts" else [result[k][i] for i in picked]
                for k in GRID_AUDIT_KEYS
            }
            want = {k: want[k] for k in GRID_AUDIT_KEYS}
        if _canon(got) == _canon(want):
            return None
        return self._mismatch(job, engine, "L2: differs from the sandbox")

    def _mismatch(self, job: SandboxJob, engine: str, why: str) -> SandboxResult:
        self._banned.add(engine)
        self._event(Event.ENGINE_AUDIT_MISMATCH, {
            "engine": engine, "kind": job.kind, "numerics": self.numerics.tag, "reason": why,
        })  # fmt: skip
        return self._refuse(why) if self.fp32 else self.sandbox.run(job)  # the canonical answer


def _canon(result: Mapping[str, Any]) -> str:
    return json.dumps(result, sort_keys=True)  # NaN serializes, so NaN == NaN here


def _json(obj: dict[str, Any]) -> dict[str, Any]:
    """What the sandbox report looks like after its JSON round trip."""
    out: dict[str, Any] = json.loads(json.dumps(obj))
    return out


def _signals(sig: Any, parsed: ParsedGenome) -> list[Signal]:
    """The rendered signal() per bar: ``Signal(direction, 1.0, stop, ratio * stop)`` or flat."""
    ratio = float(parsed.tp_sl_ratio or 0.0)
    out: list[Signal] = []
    for e, s in zip(sig.entry[:, 0], sig.stop[:, 0], strict=True):
        if e == 1:
            stop = float(s)
            out.append(Signal(parsed.direction, 1.0, stop, ratio * stop))
        else:
            out.append(FLAT)
    return out


def _features(parsed: ParsedGenome, params: Mapping[str, Any], bars: Bars) -> dict[str, Any]:
    """What the render's ``indicators()`` returns on the full series: the same registry calls on
    the same arguments — trusted code; no candidate source runs (INV-106)."""
    return {
        name: _evaluate(expr, params, bars)
        for name, expr in feature_specs(parsed.genome, parsed.direction, parsed.tp_sl_ratio)
    }


def _evaluate(expr: str, params: Mapping[str, Any], bars: Bars) -> Any:
    if expr == "bars.close":
        return bars.close
    if expr == "ind.atr(bars, 14)":
        return registry.atr(bars, 14)
    op, _, arg = expr.removeprefix("ind.").removesuffix(")").partition("(bars.close, ")
    if registry.INDICATORS.get(op) != "series":
        raise Unsupported(f"unknown feature {expr!r}")
    n = params[arg.removeprefix("self.p.")] if arg.startswith("self.p.") else int(arg)
    return getattr(registry, op)(bars.close, n)


def make_job_runner(
    sandbox: JobRunner,
    lock: Mapping[str, Any],
    compute: Compute,
    ledger_path: Path | None = None,
    run_label: str = "default",
) -> EngineRouter:
    """The runner a research session hands its gates."""
    return EngineRouter(sandbox, lock, compute, ledger_path, run_label)
