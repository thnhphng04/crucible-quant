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
from collections.abc import Callable, Mapping, Sequence
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


class EngineRefused(Exception):
    """No engine may answer a job the caller must run (the holdout's preflight, INV-110)."""


@dataclass(frozen=True)
class MemberJob:
    """A job described without its bars: what the holdout knows before it claims."""

    kind: str
    source: str
    params: Mapping[str, Any]
    symbols: tuple[str, ...]


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
        l1_rate: float = L1_RATE,
    ) -> None:
        self.sandbox = sandbox
        self.lock = lock
        self.compute = compute
        self.l1_rate = l1_rate  # the holdout checks every CUDA job (1.0)
        self.numerics = Numerics.from_lock(lock)
        self.ledger_path = ledger_path
        self.run_label = run_label
        self._local = threading.local()  # a SQLite connection is bound to its thread
        self._health_lock = threading.Lock()
        self._job_lock = threading.Lock()  # CUDA jobs run one at a time (E5)
        self._axes: dict[int, tuple[Any, list[str]]] = {}  # bars.ts -> its gate-④ axis strings
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
        if engine == CUDA and _fraction("L1", self.version, self._key(job)) < self.l1_rate:
            audit.append("L1")
            if not _same(result, self._compute(plan, job.kind, CPU)):
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
        with self._health_lock:  # one self-test per process, however many workers ask at once
            if self._cuda_ok is None:
                try:
                    from quantcrucible.execution.kernels._numba import cuda_available

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
        if job.kind not in KERNEL_KINDS:
            raise Unsupported(f"no kernel for {job.kind!r} jobs")
        if len(job.bars) != 1:
            raise Unsupported("only single-instrument spot jobs (perpetuals: P3-49..51)")
        configs = (
            [dict(c) for c in job.options["grid"]]
            if job.kind == "grid_backtest"
            else [dict(job.params)]
        )
        parsed = self._program(job.source, configs, job.options, tuple(job.bars), bool(job.perp))
        ((_, bars),) = job.bars.items()
        if not np.isfinite(np.stack([bars.open, bars.high, bars.low, bars.close])).all():
            raise Unsupported("non-finite bars")
        return _Plan(parsed, bars, configs, int(job.options["lookback"]), job.options)

    def _program(
        self,
        source: str,
        configs: list[dict[str, Any]],
        options: Mapping[str, Any],
        symbols: tuple[str, ...],
        perp: bool,
    ) -> ParsedGenome:
        """Everything about a job the kernels need except its bars: the holdout checks this
        before it claims, when it may not read the bars yet."""
        policy = options.get("exit_policy") or {}
        if policy.get("mode") != "bracket_timeout_v1":
            raise Unsupported("only bracket_timeout_v1 jobs")
        if len(symbols) != 1 or perp or ":" in symbols[0]:
            raise Unsupported("only single-instrument spot jobs (perpetuals: P3-49..51)")
        try:
            parsed = parse_genome(source)
        except GenomeParseError as exc:
            raise Unsupported(f"not a genome render: {exc}") from None
        if parsed.tp_sl_ratio is None or parsed.tp_sl_ratio != float(policy["tp_sl_ratio"]):
            raise Unsupported("the render's TP ratio differs from the locked one")
        try:
            _, program, _ = _kernels()
        except ImportError:
            raise Unsupported("the kernel engine is not installed (uv sync --group gpu)") from None
        try:
            program.compile_program(
                parsed.genome, parsed.direction, parsed.tp_sl_ratio, configs,
                int(options["lookback"]),
            )  # fmt: skip
        except program.ProgramError as exc:
            raise Unsupported(f"does not compile: {exc}") from None
        return parsed

    # ── the holdout's preflight (P3-47, INV-110) ─────────────────────────────────────────────
    def preflight(self, options: Mapping[str, Any], members: Sequence[MemberJob]) -> str:
        """Check, before the holdout is claimed, that every member job has an engine that will
        answer it, and return the engine its kernel jobs start on. Raises
        :class:`EngineRefused` otherwise — nothing has been consumed yet.

        A float32 campaign needs every member on the kernels and a working CPU build, since
        nothing else may answer it. A float64 campaign may send a member to the sandbox."""
        if self.fp32 and self.compute.engine == SANDBOX:
            raise EngineRefused("a float32 campaign cannot run on the sandbox")
        if self.compute.engine == SANDBOX:
            return SANDBOX
        kernel = False
        for m in members:
            try:
                if m.kind not in KERNEL_KINDS:
                    raise Unsupported(f"no kernel for {m.kind!r} jobs")
                perp = any(":" in s for s in m.symbols)
                self._program(m.source, [dict(m.params)], options, m.symbols, perp)
                kernel = True
            except Unsupported as exc:
                if self.fp32:
                    raise EngineRefused(f"a float32 member has no kernel: {exc}") from None
        if not kernel:
            return SANDBOX
        if not self._cpu_ready():
            if self.fp32:
                raise EngineRefused("the CPU kernel build failed its check")
            self._banned.add(CPU)
        engine = self._choose()
        if engine is None:
            if self.fp32:
                raise EngineRefused("every kernel build is refused")
            return SANDBOX
        return engine

    def _cpu_ready(self) -> bool:
        """The fallback compiles and runs: the self-test fixture on the CPU build."""
        try:
            return bool(_kernels()[0].cpu_check(self.numerics.precision))
        except Exception:
            return False

    # ── computing ────────────────────────────────────────────────────────────────────────────
    def _compute(self, plan: _Plan, kind: str, engine: str) -> dict[str, Any]:
        return self._compute_on(plan, kind, "cuda" if engine == CUDA else "cpu")

    def _signals_on(self, kb: Any, prog: Any, bars: Bars) -> Any:
        """Features and signals — the only GPU stages. One job's GPU stages run back to back
        (E5: interleaving workers' stages made them fight over transfers); the CPU replay and
        the report run outside the lock, so the device never waits on host work."""
        if kb.target == "cuda":
            with self._job_lock:
                return kb.signals(prog, bars)
        return kb.signals(prog, bars)  # numba's threading layer shares the cores

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
        # No JSON round trip, unlike the sandbox's report: every value here is already a Python
        # float/int/str (a float's repr round-trips exactly), and the return matrix stays one
        # numpy array — the gate stacks its rows anyway. A JSON pass cost ~5 s per 200-config
        # grid (E5); the report-identity tests hold the two paths to the same values.
        sig = self._signals_on(kb, prog, plan.bars)
        if kind == "grid_backtest":
            grid = kb.grid(prog, plan.bars, spec, sig)
            m = prog.n_configs
            rows = [
                GridRow([], grid.n_trades[i], grid.avg_holding_bars[i], 0.0, None) for i in range(m)
            ]
            report = grid_report(self._axis(plan.bars), rows)
            report["returns"] = grid.returns  # [configs, bars - 1]
            return report
        res = kb.backtest(prog, plan.bars, spec, sig)
        stream = signals_stream({plan.bars.symbol: _signals(sig, p)})
        corr, pair = indicator_corr([_features(p, plan.configs[0], plan.bars)])
        return backtest_report(res, corr, pair, stream)

    def _axis(self, bars: Bars) -> list[str]:
        """``[str(t) for t in bars.ts[1:]]``, built once per series: every candidate of a campaign
        shares the IS bars, and 67k ``str()`` calls cost more than the kernels do."""
        cached = self._axes.get(id(bars.ts))
        if cached is None or cached[0] is not bars.ts:  # the id may have been reused
            cached = (bars.ts, timestamps(bars.ts[1:]))
            self._axes[id(bars.ts)] = cached
        return cached[1]

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
        if _same(got, want):
            return None
        return self._mismatch(job, engine, "L2: differs from the sandbox")

    def _mismatch(self, job: SandboxJob, engine: str, why: str) -> SandboxResult:
        self._banned.add(engine)
        self._event(Event.ENGINE_AUDIT_MISMATCH, {
            "engine": engine, "kind": job.kind, "numerics": self.numerics.tag, "reason": why,
        })  # fmt: skip
        return self._refuse(why) if self.fp32 else self.sandbox.run(job)  # the canonical answer


def _same(a: Any, b: Any) -> bool:
    """Whether two results agree bit for bit, whichever engine built them: a numpy matrix and the
    lists of the sandbox's JSON report compare by their float64 bytes (NaN, -0.0 included).
    Serializing both to JSON did the same in 4-6 s per 90-config grid (E5)."""
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray) or (_floats(a) and _floats(b)):
        try:
            x, y = np.asarray(a, dtype=np.float64), np.asarray(b, dtype=np.float64)
        except (TypeError, ValueError):
            return False
        return x.shape == y.shape and x.tobytes() == y.tobytes()
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    if isinstance(a, float) and isinstance(b, float):
        return np.float64(a).tobytes() == np.float64(b).tobytes()
    return bool(a == b) and type(a) is type(b)


def _floats(v: Any) -> bool:
    """A non-empty list of floats, or of such lists (a return matrix in list form)."""
    if not isinstance(v, list) or not v:
        return False
    head = v[0]
    if isinstance(head, list):
        return all(isinstance(r, list) and all(type(x) is float for x in r) for r in v)
    return all(type(x) is float for x in v)


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
