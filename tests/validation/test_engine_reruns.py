"""Gate ⑥′ and calibration on the kernel engine (P3-46, ADR-0038).

Both re-run members through ``ctx.services["sandbox"]``, which every research session fills with
the engine router (``cli._session``). Here each path runs twice — once against the canonical job
functions (the fake sandbox), once through the router — and must give the same numbers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

pytest.importorskip("numba")

from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.calibration import calibrate
from quantcrucible.validation.gates import GateContext, GatePipeline, StrategyCandidate
from quantcrucible.validation.is_gates import InSampleGate, backtest_options
from quantcrucible.validation.pbo_gate import PboGate
from quantcrucible.validation.portfolio import Member
from quantcrucible.validation.robustness import (
    COST_MULTIPLIER,
    account_options,
    rerun_member_report,
)
from tests.validation.test_engine_router import FakeSandbox, _bars, _canon, _genome_job, _router

LOCK: dict[str, Any] = {
    "campaign_id": "c1",
    "research": {
        "minbtl_target_sharpe": 1.5,
        "max_risk_pct": 0.01,
        "portfolio": {"rebalance": "monthly"},
        "constraints": {"min_trades": 1, "min_holding_bars": 1, "max_indicator_corr": 0.99},
        "exit": {"tp_sl_ratio": 1.1, "max_holding_bars": 100},
        "pbo_grid": {"values_per_param": 3, "range": 0.3, "max_configs": 4},
        "gates": {"pbo_max": 0.5, "dsr_min": 0.95},
    },
    "derived": {
        "costs": {"fee_rate": 0.001, "slippage_bps": 5.0},
        "lookback": 120,
        "sizing": {"rule": "risk_over_stop"},
        "exit_protocol": "bracket_timeout_v1",
        "pbo": {"n_splits": 4},
    },
}


def _candidate() -> StrategyCandidate:
    job = _genome_job()
    return StrategyCandidate(
        candidate_id="g", source=job.source, params=dict(job.params), universe=("BTC/USDT",),
        timeframe="1h", timerange="t", run_id="r", campaign_id="c1",
    )  # fmt: skip


def _calibrate(tmp_path: Path, runner: Any) -> tuple[Any, Ledger]:
    tmp_path.mkdir()
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    services = {"sandbox": runner, "is_data": _bars(), "results_dir": tmp_path / "res"}
    ctx = GateContext(ledger, LOCK, services)
    full = GatePipeline([InSampleGate(), PboGate()])
    return calibrate(_candidate(), ctx, budget=4, full_pipeline=full), ledger


def test_calibration_measures_on_the_kernel_with_the_sandbox_numbers(tmp_path: Path) -> None:
    want, canon = _calibrate(tmp_path / "canon", FakeSandbox())
    box = FakeSandbox()
    got, ledger = _calibrate(tmp_path / "router", _router(box, LOCK))
    assert not box.jobs  # every Optuna attempt and the confirmation ran on the kernel
    assert [(a.params, a.outcome, a.sharpe_is) for a in got.attempts] == [
        (a.params, a.outcome, a.sharpe_is) for a in want.attempts
    ]
    assert got.best_params == want.best_params and got.best_sharpe == want.best_sharpe
    rows = ledger.trials("c1")
    assert len(rows) == len(canon.trials("c1")) >= 4  # every attempt is a trial
    assert [np.float64(t.sharpe_is).tobytes() for t in rows] == [
        np.float64(t.sharpe_is).tobytes() for t in canon.trials("c1")
    ]
    assert {t.backtest_engine for t in rows} == {"cpu_kernel"}
    assert {t.backtest_engine for t in canon.trials("c1")} == {"sandbox"}


@pytest.mark.parametrize("source", ["primary", "second"])
def test_a_robustness_rerun_matches_the_sandbox(source: str) -> None:
    """Costs × 2 on the primary bars, and the locked costs on a second source's bars."""
    job = _genome_job()
    member = Member(1, "g", "h", dict(job.params), 1.0, ("BTC/USDT",), "1h", "long")
    base = account_options(LOCK, backtest_options(LOCK, seed=0))
    options = base
    bars = _bars()
    if source == "primary":
        options = {**base, "costs": {k: v * COST_MULTIPLIER for k, v in base["costs"].items()}}
    else:
        rng = np.random.default_rng(9)
        b = bars["BTC/USDT"]
        wiggle = 1 + rng.normal(0, 0.001, len(b))
        second = type(b)(b.symbol, b.timeframe, b.ts, b.open * wiggle, b.high * wiggle,
                         b.low * wiggle, b.close * wiggle, b.volume)  # fmt: skip
        bars = {"BTC/USDT": second}
    want = rerun_member_report(member, job.source, bars, options, FakeSandbox())
    box = FakeSandbox()
    got = rerun_member_report(member, job.source, bars, options, _router(box, LOCK))
    assert not box.jobs and _canon(got) == _canon(want)


def test_a_float32_rerun_stays_on_the_kernel() -> None:
    job = _genome_job()
    member = Member(1, "g", "h", dict(job.params), 1.0, ("BTC/USDT",), "1h", "long")
    lock32 = {
        **LOCK,
        "research": {**LOCK["research"], "backtest": {"precision": "float32"}},
        "derived": {**LOCK["derived"], "backtest_numerics": "fp32_signals_v1"},
    }
    options = account_options(lock32, backtest_options(lock32, seed=0))
    box = FakeSandbox()
    got = rerun_member_report(member, job.source, _bars(), options, _router(box, lock32))
    assert not box.jobs and got["returns"]
