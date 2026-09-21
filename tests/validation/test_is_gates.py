"""Gates ② MinBTL and ③ IS (Architecture §3.2, D15) — INV-38. The sandbox is faked here; the
real container path is covered by tests/e2e/test_phase0.py."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quantcrucible.core.strategy.registry import ind
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import TrialRecord
from quantcrucible.validation.feature_stats import max_abs_change_corr
from quantcrucible.validation.gates import G3_IS, GateContext, GatePipeline, StrategyCandidate
from quantcrucible.validation.is_gates import InSampleGate, MinBtlGate
from quantcrucible.validation.sandbox import SandboxJob, SandboxResult
from quantcrucible.validation.statistical import min_btl_years
from tests.factories import make_bars

LOCK = {
    "research": {
        "minbtl_target_sharpe": 1.5,
        "constraints": {"min_trades": 30, "min_holding_bars": 1, "max_indicator_corr": 0.9},
    },
    "derived": {"costs": {"fee_rate": 0.001, "slippage_bps": 5.0}, "lookback": 400},
}


class FakeRunner:
    def __init__(self, **overrides: Any) -> None:
        self.overrides = overrides
        self.jobs: list[SandboxJob] = []

    def run(self, job: SandboxJob) -> SandboxResult:
        self.jobs.append(job)
        public = {
            "sharpe_is": 0.8, "max_drawdown": 0.2, "total_return": 0.5, "n_trades": 40.0,
            "avg_holding_bars": 12.0, "turnover": 3.0,
        }  # fmt: skip
        public.update(self.overrides.get("public", {}))
        result = {
            "public": public, "ts": ["2020-01-02", "2020-01-03", "2020-01-04"],
            "returns": [0.01, -0.005], "equity": [1.0, 1.01, 1.005], "denied_orders": 0,
            "indicator_corr": self.overrides.get("corr", 0.5),
            "indicator_pair": ["f", "s"],
        }  # fmt: skip
        return SandboxResult(True, {"ok": True, "result": result}, "", "", 0, False, None, 0.1)


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    led = Ledger.open(tmp_path / "ledger.db")
    led.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    return led


def ctx(ledger: Ledger, tmp_path: Path, n_bars: int = 800, **fake: Any) -> GateContext:
    data = {"BTC/USDT": make_bars(n_bars, symbol="BTC/USDT")}
    services = {"sandbox": FakeRunner(**fake), "is_data": data, "results_dir": tmp_path / "res"}
    return GateContext(ledger=ledger, lock=LOCK, services=services)


def cand(cid: str = "x") -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=cid, source="s", params={}, universe=("BTC/USDT",), timeframe="1d",
        timerange="t", run_id="r", campaign_id="c1",
    )  # fmt: skip


def add_trials(ledger: Ledger, n: int) -> None:
    for i in range(n):
        ledger.record_trial(
            TrialRecord(
                run_id="r", campaign_id="c1", candidate_id=f"t{i}", engine="manual", seed=0,
                strategy_hash=f"h{i}", params={}, universe="BTC/USDT", timeframe="1d",
                timerange="t", source="manual", sharpe_is=0.1 * i, returns_path=f"r{i}.parquet",
                verdict="PASS",
            )
        )  # fmt: skip


# ── ② ────────────────────────────────────────────────────────────────────


def test_g2_passes_long_history(ledger: Ledger, tmp_path: Path) -> None:
    result = MinBtlGate().check(cand(), ctx(ledger, tmp_path, n_bars=800))
    assert result.passed and result.detail and result.detail["n_trials"] == 1


def test_g2_rejects_when_trials_outgrow_the_data(ledger: Ledger, tmp_path: Path) -> None:
    add_trials(ledger, 99)  # N = 100 ⇒ MinBTL ≈ 2.85 years at target 1.5
    assert min_btl_years(100, 1.5) > 800 / 365.25
    result = MinBtlGate().check(cand(), ctx(ledger, tmp_path, n_bars=800))
    assert not result.passed and "N=100" in result.reason
    assert MinBtlGate().check(cand(), ctx(ledger, tmp_path, n_bars=1_200)).passed


# ── ③ ────────────────────────────────────────────────────────────────────


def test_g3_passes_and_saves_returns(ledger: Ledger, tmp_path: Path) -> None:
    c = ctx(ledger, tmp_path)
    result = InSampleGate().check(cand("ok"), c)
    assert result.passed and result.measurement is not None and result.report is not None
    saved = pd.read_parquet(result.measurement.returns_path)
    np.testing.assert_allclose(saved["ret"], [0.01, -0.005])
    assert result.report.public["n_trades"] == 40.0
    job = c.services["sandbox"].jobs[0]
    assert job.kind == "backtest" and job.options["costs"]["fee_rate"] == 0.001


@pytest.mark.parametrize(
    ("fake", "fragment"),
    [
        ({"public": {"n_trades": 29.0}}, "min_trades"),
        ({"public": {"avg_holding_bars": 0.5}}, "min_holding_bars"),
        ({"corr": 0.95}, "max_indicator_corr"),
    ],
)
def test_g3_constraints(
    ledger: Ledger, tmp_path: Path, fake: dict[str, Any], fragment: str
) -> None:
    result = InSampleGate().check(cand(), ctx(ledger, tmp_path, **fake))
    assert not result.passed and fragment in result.reason
    assert result.measurement is not None  # measured ⇒ still a trial


def test_g3_reject_is_still_a_trial_row(ledger: Ledger, tmp_path: Path) -> None:
    c = ctx(ledger, tmp_path, public={"n_trades": 3.0})
    outcome = GatePipeline([MinBtlGate(), InSampleGate()]).run(cand("few"), c)
    assert outcome.failed_gate == G3_IS and outcome.trial_id is not None
    assert ledger.trial_verdicts("c1") == [(outcome.trial_id, "few", f"REJECT_{G3_IS}")]


# ── indicator correlation on changes ─────────────────────────────────────


def test_change_correlation_ignores_shared_trend() -> None:
    bars = make_bars(1_000, seed=2)
    fast, slow = ind.ema(bars.close, 20), ind.ema(bars.close, 100)
    level = abs(float(np.corrcoef(fast[100:], slow[100:])[0, 1]))
    rho, pair = max_abs_change_corr({"f": fast, "s": slow, "atr": ind.atr(bars, 14)})
    assert level > 0.9 > rho and pair == ("f", "s")
    assert max_abs_change_corr({"a": fast, "b": fast * 2 + 1})[0] == pytest.approx(1.0)
    assert max_abs_change_corr({"a": fast, "flat": np.ones(1_000)}) == (0.0, None)
