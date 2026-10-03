"""The engine router (P3-42, ADR-0038/0039; INV-106, INV-109, INV-110).

The fake sandbox answers with the real in-container job functions run in-process on trusted,
test-built genomes — the canonical results the kernel engine must reproduce."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("numba")

from quantcrucible.config.schema import Compute
from quantcrucible.core.strategy.base import Bars
from quantcrucible.core.strategy.genome import (
    Combine,
    Cross,
    Genome,
    Indicator,
    Param,
    Slope,
    render_genome,
)
from quantcrucible.core.strategy.template import load_strategy_class
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation import sandbox_runner
from quantcrucible.validation.engine_router import (
    EngineRefused,
    EngineRouter,
    MemberJob,
    _fraction,
    _same,
)
from quantcrucible.validation.sandbox import SandboxJob, SandboxResult

ZOO = Path(sandbox_runner.__file__).resolve().parents[1] / "core" / "zoo" / "ema_crossover.py"
OPTIONS: dict[str, Any] = {
    "lookback": 120,
    "risk": {"max_risk_pct": 0.01},
    "costs": {"fee_rate": 0.001, "slippage_bps": 5.0},
    "exit_policy": {"mode": "bracket_timeout_v1", "tp_sl_ratio": 1.1, "max_holding_bars": 100},
    "seed": 0,
}
FP64 = {"campaign_id": "c1", "research": {}, "derived": {}}
FP32 = {"campaign_id": "c1", "research": {"backtest": {"precision": "float32"}},
        "derived": {"backtest_numerics": "fp32_signals_v1"}}  # fmt: skip


class FakeSandbox:
    """Runs the job functions the container would run; can be told to lie."""

    def __init__(self, tamper: bool = False, canned: bool = False) -> None:
        self.jobs: list[SandboxJob] = []
        self.tamper = tamper
        self.canned = canned  # record the job, answer without running it
        self.killed = 0

    def run(self, job: SandboxJob) -> SandboxResult:
        self.jobs.append(job)
        if job.kind == "leak_check" or self.canned:
            report: dict[str, Any] = {"ok": True, "kind": job.kind, "result": {"n_leaks": 0}}
            return SandboxResult(True, report, "", "", 0, False, None, 0.1)
        cls = load_strategy_class(job.source, f"fake_{len(self.jobs)}")
        spec = {**job.options, "params": dict(job.params), "perp_inputs": dict(job.perp or {})}
        result = sandbox_runner.JOBS[job.kind](cls, dict(job.bars), spec)
        result = json.loads(json.dumps(result))
        if self.tamper and job.kind == "grid_backtest":
            result["returns"][0][0] += 1e-6
        elif self.tamper:
            result["public"]["n_trades"] += 1
        return SandboxResult(True, {"ok": True, "kind": job.kind, "result": result}, "", "", 0,
                             False, None, 0.1)  # fmt: skip

    def kill_all(self) -> int:
        self.killed += 1
        return 3


def _bars(n: int = 600) -> dict[str, Bars]:
    rng = np.random.default_rng(3)
    close = 20_000 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.r_[close[0], close[:-1]]
    high, low = np.maximum(open_, close) * 1.004, np.minimum(open_, close) * 0.996
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "h")
    return {"BTC/USDT": Bars("BTC/USDT", "1h", ts, open_, high, low, close, np.ones(n))}


STOPS = ["fixed", "atr_gene", "bollinger_gene"]  # ADR-0041: with and without the n_stop gene


def _genome_job(kind: str = "backtest", stop: str = "fixed") -> SandboxJob:
    def n(v: int) -> Param:
        return Param("period", 2, 300, v)

    g = Genome(
        Combine("and", (Cross(Indicator("ema", n(10)), True, Indicator("ema", n(30))),
                        Slope(Indicator("sma", n(20)), True))),
        Param("mult", 0.5, 5.0, 1.5),
        stop_kind="bollinger" if stop == "bollinger_gene" else "atr",
        stop_period=None if stop == "fixed" else Param("period", 5, 50, 21),
    )  # fmt: skip
    src, params = render_genome(g, "long", 1.1)
    options = dict(OPTIONS)
    if kind == "grid_backtest":
        options["grid"] = [
            params,
            {**params, "n1": 12},
            {**params, "k_stop": 2.0},
            {**params, "n3": 25},
        ] + ([] if stop == "fixed" else [{**params, "n_stop": 9}, {**params, "n_stop": 40}])
    return SandboxJob(kind, src, _bars(), params, options)


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    path = tmp_path / "ledger.db"
    Ledger.open(path).open_campaign("c1", "2025-09-21/2026-09-21", lock_hash="abc")
    return path


def _router(sandbox: FakeSandbox, lock: dict[str, Any], ledger_path: Path | None = None,
            engine: str = "cpu_kernel", audit: float = 0.0) -> EngineRouter:  # fmt: skip
    return EngineRouter(sandbox, lock, Compute(engine, audit), ledger_path)  # type: ignore[arg-type]


def _canon(result: Any) -> str:
    return json.dumps(result, sort_keys=True, default=lambda a: a.tolist())


@pytest.mark.parametrize("stop", STOPS)
@pytest.mark.parametrize("kind", ["backtest", "grid_backtest"])
def test_a_genome_job_runs_on_the_kernel_and_reports_like_the_sandbox(kind: str, stop: str) -> None:
    job = _genome_job(kind, stop)
    box = FakeSandbox()
    res = _router(box, FP64).run(job)
    assert res.ok and res.engine == "cpu_kernel" and not box.jobs
    assert res.report is not None
    canonical = FakeSandbox().run(job).report
    assert canonical is not None
    assert _canon(res.report["result"]) == _canon(canonical["result"])  # every value
    assert res.report["kernel_version"] and res.report["numerics"] == "fp64_oracle_v1"
    if kind == "grid_backtest":  # what pbo_gate does with the rows: identical matrices
        rows: list[Any] = []
        rows.extend(res.report["result"]["returns"])
        want = np.asarray(canonical["result"]["returns"], dtype=np.float64)
        assert np.array_equal(np.asarray(rows, dtype=np.float64), want)


@pytest.mark.parametrize("kind", ["backtest", "grid_backtest"])
def test_a_float32_genome_job_runs_on_the_kernel_and_never_touches_the_sandbox(kind: str) -> None:
    """P3-45: float32 features and signals, a float64 replay; no float64 audit (INV-110)."""
    job = _genome_job(kind)
    box = FakeSandbox()
    res = _router(box, FP32, audit=1.0).run(job)
    assert res.ok and res.engine == "cpu_kernel" and not box.jobs
    assert res.report is not None and res.report["numerics"] == "fp32_signals_v1"
    assert res.report["audit"] == "none"
    fp64 = _router(FakeSandbox(), FP64).run(job).report
    assert fp64 is not None
    got, want = res.report["result"], fp64["result"]
    if kind == "grid_backtest":
        assert np.asarray(got["returns"]).dtype == np.float64
        assert np.allclose(got["returns"], want["returns"], atol=1e-6)
    else:
        assert got["public"]["n_trades"] == want["public"]["n_trades"]


def test_leak_checks_always_use_the_sandbox() -> None:
    job = _genome_job()
    for lock in (FP64, FP32):
        box = FakeSandbox()
        _router(box, lock).run(SandboxJob("leak_check", job.source, job.bars, job.params, {}))
        assert [j.kind for j in box.jobs] == ["leak_check"]


def test_a_hand_written_strategy_uses_the_sandbox_in_float64_and_is_refused_in_float32() -> None:
    zoo = SandboxJob("backtest", ZOO.read_text("utf-8"), _bars(), {}, OPTIONS)
    box = FakeSandbox(canned=True)
    assert _router(box, FP64).run(zoo).engine == "sandbox" and len(box.jobs) == 1
    box32 = FakeSandbox(canned=True)
    res = _router(box32, FP32).run(zoo)
    assert not res.ok and res.engine == "refused" and not box32.jobs  # INV-110: no fp64 fallback


def test_the_sandbox_setting_bypasses_the_kernels() -> None:
    box = FakeSandbox()
    res = _router(box, FP64, engine="sandbox").run(_genome_job())
    assert res.engine == "sandbox" and len(box.jobs) == 1


def test_an_honest_audit_keeps_the_kernel_result(ledger_path: Path) -> None:
    box = FakeSandbox()
    res = _router(box, FP64, ledger_path, audit=1.0).run(_genome_job("grid_backtest"))
    assert res.engine == "cpu_kernel" and res.report is not None and res.report["audit"] == "L2"
    [probe] = box.jobs
    assert len(probe.options["grid"]) == 2  # config 0 and one other, not the whole grid
    assert not Ledger.open(ledger_path).events_named(Event.ENGINE_AUDIT_MISMATCH)


def test_an_audit_mismatch_answers_canonically_and_bans_the_build(ledger_path: Path) -> None:
    """INV-109: the canonical result is used and the kernel version is refused for good."""
    liar = FakeSandbox(tamper=True)
    res = _router(liar, FP64, ledger_path, audit=1.0).run(_genome_job())
    assert res.engine == "sandbox"  # the canonical answer, not the kernel's
    [event] = Ledger.open(ledger_path).events_named(Event.ENGINE_AUDIT_MISMATCH)
    assert event["engine"] == "cpu_kernel" and event["kernel_version"]
    box = FakeSandbox()
    again = _router(box, FP64, ledger_path).run(_genome_job())  # a new process, same ledger
    assert again.engine == "sandbox" and len(box.jobs) == 1


def test_a_kernel_fault_falls_back_in_float64_and_refuses_in_float32(
    ledger_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("device lost")

    monkeypatch.setattr(EngineRouter, "_compute_on", boom)
    box = FakeSandbox()
    assert _router(box, FP64, ledger_path).run(_genome_job()).engine == "sandbox"
    assert Ledger.open(ledger_path).events_named(Event.ENGINE_FALLBACK)
    box32 = FakeSandbox()
    assert not _router(box32, FP32, ledger_path).run(_genome_job()).ok and not box32.jobs


def test_the_holdout_preflight_names_the_engine_or_refuses() -> None:
    """P3-47: checked from sources and parameters alone, before any bar is read."""
    job = _genome_job()
    genome = MemberJob("backtest", job.source, job.params, ("BTC/USDT",))
    zoo = MemberJob("backtest", ZOO.read_text("utf-8"), {}, ("BTC/USDT",))
    perp = MemberJob("signals", job.source, job.params, ("BTC/USDT:USDT",))
    assert _router(FakeSandbox(), FP64).preflight(OPTIONS, [genome, zoo]) == "cpu_kernel"
    assert _router(FakeSandbox(), FP64).preflight(OPTIONS, [zoo]) == "sandbox"
    assert _router(FakeSandbox(), FP64, engine="sandbox").preflight(OPTIONS, [genome]) == "sandbox"
    assert _router(FakeSandbox(), FP32).preflight(OPTIONS, [genome]) == "cpu_kernel"
    assert _router(FakeSandbox(), FP32).preflight(OPTIONS, [perp]) == "cpu_kernel"  # P3-51
    with pytest.raises(EngineRefused, match="float32 member"):
        _router(FakeSandbox(), FP32).preflight(OPTIONS, [genome, zoo])
    with pytest.raises(EngineRefused, match="sandbox"):
        _router(FakeSandbox(), FP32, engine="sandbox").preflight(OPTIONS, [genome])


def test_audits_compare_results_bit_for_bit() -> None:
    m = np.array([[0.1, np.nan], [-0.0, 2.0]])
    assert _same({"r": m, "n": [3, 4]}, {"r": [[0.1, float("nan")], [-0.0, 2.0]], "n": [3, 4]})
    assert not _same(m, [[0.1, float("nan")], [0.0, 2.0]])  # -0.0 is not 0.0
    assert not _same({"n": 3}, {"n": 3.0})  # an int is not a float
    assert not _same([1.0, 2.0], [1.0, 2.0, 3.0])
    assert _same(["flat", 0.0, 0.0, None], ["flat", 0.0, 0.0, None])


def test_audit_sampling_is_deterministic() -> None:
    assert _fraction("L2", "v", "job") == _fraction("L2", "v", "job")
    assert _fraction("L2", "v", "job") != _fraction("L2", "v2", "job")


def test_kill_all_reaches_the_sandbox() -> None:
    box = FakeSandbox()
    assert _router(box, FP64).kill_all() == 3 and box.killed == 1
