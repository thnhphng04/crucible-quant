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

LOCK: dict[str, Any] = {
    "research": {
        "minbtl_target_sharpe": 1.5,
        "max_risk_pct": 0.01,
        "portfolio": {"rebalance": "monthly"},
        "constraints": {"min_trades": 30, "min_holding_bars": 1, "max_indicator_corr": 0.9},
    },
    "derived": {
        "costs": {"fee_rate": 0.001, "slippage_bps": 5.0},
        "lookback": 400,
        "sizing": {"rule": "risk_over_stop"},
    },
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
            "signals_stream": {
                s: [["long", 1.0, 2.0, None], ["flat", 0.0, 0.0, None], ["long", 1.0, 2.5, None]]
                for s in job.bars
            },
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
    assert job.options["risk"] == {"max_risk_pct": 0.01}


def test_g3_refuses_a_lock_without_sizing(ledger: Ledger, tmp_path: Path) -> None:
    """A campaign opened before the Risk layer must not mix two sizing rules (ADR-0010)."""
    old = {**LOCK, "derived": {k: v for k, v in LOCK["derived"].items() if k != "sizing"}}
    c = ctx(ledger, tmp_path)
    c.lock = old
    with pytest.raises(ValueError, match="open a new campaign"):
        InSampleGate().check(cand("ok"), c)


def test_g3_refuses_a_lock_written_under_vol_targeting(ledger: Ledger, tmp_path: Path) -> None:
    """ADR-0031: a v4 lock carries `derived.sizing.max_leverage`. Its trials were sized under
    P4, so they must not be mixed with `Q = R/d` trials inside one campaign."""
    old = {
        **LOCK,
        "derived": {**LOCK["derived"], "sizing": {"vol_span": 25, "max_leverage": 1.0}},
    }
    c = ctx(ledger, tmp_path)
    c.lock = old
    with pytest.raises(ValueError, match="vol targeting"):
        InSampleGate().check(cand("ok"), c)


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


@pytest.mark.parametrize("key", ["min_trades", "min_holding_bars", "max_indicator_corr"])
def test_g3_nan_threshold_fails_closed(ledger: Ledger, tmp_path: Path, key: str) -> None:
    c = ctx(ledger, tmp_path, corr=1.0 if key == "max_indicator_corr" else 0.5)
    constraints = {**LOCK["research"]["constraints"], key: float("nan")}
    c.lock = {**LOCK, "research": {**LOCK["research"], "constraints": constraints}}
    assert not InSampleGate().check(cand(), c).passed


def test_g3_nan_measurement_fails_closed(ledger: Ledger, tmp_path: Path) -> None:
    assert not InSampleGate().check(cand(), ctx(ledger, tmp_path, corr=float("nan"))).passed


def test_g3_artifacts_are_immutable_per_measurement(ledger: Ledger, tmp_path: Path) -> None:
    """Re-measuring the same candidate id (calibration's confirmation run) must not rewrite the
    returns an earlier trial row points to (review finding 1)."""
    first = InSampleGate().check(cand("same"), ctx(ledger, tmp_path))
    second = InSampleGate().check(cand("same"), ctx(ledger, tmp_path, public={"sharpe_is": 1.2}))
    assert first.measurement is not None and second.measurement is not None
    assert first.measurement.returns_path != second.measurement.returns_path
    saved = pd.read_parquet(first.measurement.returns_path)
    np.testing.assert_allclose(saved["ret"], [0.01, -0.005])


# ── the perpetual path through gate ③ (P3-21) ─────────────────────────────────────────


def perp_ctx(
    ledger: Ledger, tmp_path: Path, *, with_inputs: bool = True, **fake: Any
) -> GateContext:
    from quantcrucible.core.path_summary import segment_bar
    from quantcrucible.core.perp_inputs import PerpBundle

    series = make_bars(800, symbol="BTC/USDT:USDT")
    flat = [100.0, 100.0]
    bundle = PerpBundle(
        symbol=series.symbol,
        timeframe=series.timeframe,
        marks=series,
        funding=np.zeros((0, 4), dtype=np.float64),
        paths=tuple(segment_bar([0, 1], flat, flat, cuts=[]) for _ in range(len(series))),
        brackets=({"cap": 1e9, "max_leverage": 100, "mmr": 0.004, "amount": 0.0},),
    )
    services: dict[str, Any] = {
        "sandbox": FakeRunner(**fake),
        "is_data": {series.symbol: series},
        "results_dir": tmp_path / "res",
    }
    if with_inputs:
        services["perp_data"] = {series.symbol: bundle}
    return GateContext(ledger=ledger, lock=LOCK, services=services)


def perp_cand() -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id="p", source="s", params={}, universe=("BTC/USDT:USDT",), timeframe="1d",
        timerange="t", run_id="r", campaign_id="c1",
    )  # fmt: skip


def test_g3_sends_the_perpetual_bundle_into_the_sandbox(ledger: Ledger, tmp_path: Path) -> None:
    context = perp_ctx(ledger, tmp_path)
    InSampleGate().check(perp_cand(), context)
    job = context.services["sandbox"].jobs[-1]
    assert job.perp is not None and set(job.perp) == {"BTC/USDT:USDT"}


def test_g3_refuses_a_perpetual_candidate_with_no_mark_or_funding(
    ledger: Ledger, tmp_path: Path
) -> None:
    """Fail closed (INV-94). Running it anyway would report a Sharpe measured on a market with
    no funding cost and no liquidation — a number that looks like every other Sharpe."""
    context = perp_ctx(ledger, tmp_path, with_inputs=False)
    result = InSampleGate().check(perp_cand(), context)
    assert not result.passed
    assert "perpetual" in result.reason


def test_g3_leaves_a_spot_candidate_alone(ledger: Ledger, tmp_path: Path) -> None:
    context = ctx(ledger, tmp_path)
    InSampleGate().check(cand(), context)
    assert context.services["sandbox"].jobs[-1].perp is None


def test_g3_writes_the_signal_stream_a_portfolio_replay_will_need(
    ledger: Ledger, tmp_path: Path
) -> None:
    """ADR-0035: a portfolio sizes from signals, so gate ③ is where the stream is captured. It
    is written beside the returns curve, under the same artifact convention."""
    context = perp_ctx(ledger, tmp_path)
    InSampleGate().check(perp_cand(), context)
    returns = list((tmp_path / "res" / "returns" / "c1" / "p").glob("*.parquet"))
    signals = list((tmp_path / "res" / "signals" / "c1" / "p").glob("*.parquet"))
    assert len(returns) == len(signals) == 1
    assert returns[0].name == signals[0].name, "the two must share one id so one locates the other"
    frame = pd.read_parquet(signals[0])
    assert list(frame.columns) == [
        "symbol", "bar", "direction", "strength", "stop_distance", "take_profit",
    ]  # fmt: skip
    assert len(frame) == 3  # the fake runner returns a three-bar stream
