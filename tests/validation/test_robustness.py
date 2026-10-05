"""Gate ⑥′ — costs × 2 and a second data source (§3.2 ⑥′, §6.1, ADR-0015). A fake sandbox
turns (params, data, costs) into returns, so no Docker is needed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quantcrucible.core.strategy.base import Bars
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import GateResultRecord, PortfolioVariant, TrialRecord
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.gates import G4_PBO, GateContext
from quantcrucible.validation.portfolio import PortfolioRule, build_and_record
from quantcrucible.validation.robustness import RobustnessGate
from quantcrucible.validation.sandbox import SandboxJob, SandboxResult
from tests.factories import make_bars
from tests.validation.test_is_gates import LOCK as IS_LOCK
from tests.validation.test_pbo_gate import ZOO

N = 1500
LOCK: dict[str, Any] = {
    **IS_LOCK,
    "research": {**IS_LOCK["research"], "gates": {"dsr_min": 0.95}},
}
SECOND_TAG = 7.0  # the fake's way to recognise second-source bars (their volume)


def fake_returns(params: dict[str, Any], bars: Bars, fee_rate: float) -> np.ndarray:
    """edge − fees, plus noise that depends on the member and the data (not on the costs)."""
    on_second = bars.volume[0] == SECOND_TAG
    edge = float(params["edge"]) * (float(params.get("second_factor", 0.5)) if on_second else 1.0)
    rng = np.random.default_rng(int(params["seed"]) * 1000 + int(bars.close[0] * 100) % 997)
    return edge - fee_rate + rng.normal(0, 0.01, len(bars))


class FakeBacktester:
    def __init__(self) -> None:
        self.jobs: list[SandboxJob] = []

    def run(self, job: SandboxJob) -> SandboxResult:
        self.jobs.append(job)
        bars = next(iter(job.bars.values()))
        rets = fake_returns(dict(job.params), bars, float(job.options["costs"]["fee_rate"]))
        result = {"ts": [str(t) for t in bars.ts], "returns": rets[1:].tolist()}
        return SandboxResult(True, {"ok": True, "result": result}, "", "", 0, False, None, 0.1)


def data(second: bool = False, hours: int | None = None) -> dict[str, Bars]:
    """``hours`` bars an hour apart instead of ``N`` daily ones."""
    b = make_bars(hours or N, seed=3, symbol="BTC/USDT")
    if hours is not None:
        ts = b.ts[0] + np.arange(hours) * np.timedelta64(1, "h")
        b = Bars(b.symbol, "1h", ts, b.open, b.high, b.low, b.close, b.volume)
    if second:
        b = Bars(b.symbol, b.timeframe, b.ts, b.open, b.high, b.low, b.close,
                 np.full(len(b), SECOND_TAG))  # fmt: skip
    return {"BTC/USDT": b}


def setup(
    tmp_path: Path,
    edges: list[float],
    second_factor: float = 1.0,
    hours: int | None = None,
    clock: str | None = None,
) -> tuple[Ledger, Any, dict[str, Any]]:
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2030-01-01/2031-01-01", lock_hash="h")
    archive = StrategyArchive(tmp_path / "strategies")
    s_hash = archive.put(ZOO)
    bars = data(hours=hours)["BTC/USDT"]
    for i, edge in enumerate(edges):
        params = {"edge": edge, "seed": i, "second_factor": second_factor}
        rets = fake_returns(params, bars, 0.001)[1:]
        path = tmp_path / f"m{i}.parquet"
        pd.DataFrame({"ts": bars.ts[1:], "ret": rets}).to_parquet(path, index=False)
        tid = ledger.record_trial(
            TrialRecord(
                run_id="r", campaign_id="c1", candidate_id=f"m{i}", engine="manual", seed=0,
                strategy_hash=s_hash, params=params, universe="BTC/USDT", timeframe="1d",
                timerange="t", source="manual", sharpe_is=1.0, returns_path=str(path),
                verdict="PASS",
            )
        )  # fmt: skip
        ledger.record_gate_result(
            GateResultRecord(campaign_id="c1", candidate_id=f"m{i}", gate=G4_PBO, passed=True,
                             reason="x", trial_id=tid)
        )  # fmt: skip
    ppy = 365.0 if hours is None else 8760.0
    portfolio = build_and_record(ledger, "c1", PortfolioRule(), ppy, tmp_path / "res", clock)
    services = {"sandbox": FakeBacktester(), "archive": archive, "is_data": data(hours=hours),
                "second_is_data": data(second=True, hours=hours)}  # fmt: skip
    return ledger, portfolio, services


def run(tmp_path: Path, edges: list[float], **kw: Any) -> Any:
    ledger, portfolio, services = setup(tmp_path, edges, kw.pop("second_factor", 1.0))
    services.update(kw)
    return RobustnessGate().check(portfolio, GateContext(ledger, LOCK, services)), services


def test_robust_portfolio_passes(tmp_path: Path) -> None:
    result, services = run(tmp_path, [0.004, 0.004])
    assert result.passed, result.reason
    d = result.detail
    assert d["sharpe_stressed"] > 0 and d["dsr_stressed_n_eff"] >= 0.95
    assert abs(d["sharpe_drop"]) < 0.05  # the same edge on the second source
    stressed_jobs = [j for j in services["sandbox"].jobs if j.options["costs"]["fee_rate"] == 0.002]
    assert len(stressed_jobs) == 2 and stressed_jobs[0].options["costs"]["slippage_bps"] == 10.0


def test_edge_eaten_by_doubled_costs_fails(tmp_path: Path) -> None:
    result, _ = run(tmp_path, [0.0015, 0.0015])  # net +0.0005/day, −0.0005/day at 2× fees
    assert not result.passed and "costs × 2" in result.reason and "not > 0" in result.reason


def test_edge_that_vanishes_on_the_second_source_fails(tmp_path: Path) -> None:
    result, _ = run(tmp_path, [0.004, 0.004], second_factor=0.5)
    assert not result.passed and "second source" in result.reason
    assert result.detail["sharpe_drop"] > 0.30


def test_missing_second_source_fails_closed(tmp_path: Path) -> None:
    result, _ = run(tmp_path, [0.004, 0.004], second_is_data={})
    assert not result.passed and "second-source" in result.reason


def test_the_daily_clock_judges_hourly_reruns_day_by_day(tmp_path: Path) -> None:
    """ADR-0049: costs × 2 is read on UTC-day returns × √365, as gate ⑤ reads the portfolio."""
    hours = 24 * 90
    ledger, portfolio, services = setup(tmp_path, [0.0005], hours=hours, clock="daily_v1")
    result = RobustnessGate().check(portfolio, GateContext(ledger, LOCK, services))
    bars = data(hours=hours)["BTC/USDT"]
    member = pd.Series(
        fake_returns(dict(portfolio.members[0].params), bars, 0.002)[1:],
        index=pd.DatetimeIndex(bars.ts[1:]),
    )
    day_of_bar = (pd.DatetimeIndex(member.index) - pd.Timedelta(1, "ns")).floor("D")
    days = (1.0 + member).groupby(day_of_bar).prod() - 1.0
    by_hand = days.mean() / days.std(ddof=1) * np.sqrt(365)
    assert result.detail is not None
    assert result.detail["sharpe_stressed"] == pytest.approx(by_hand, rel=1e-9)


@pytest.mark.parametrize(("clock", "unit"), [("daily_v1", "days"), (None, "bars")])
def test_the_second_source_needs_sixty_common_observations_on_the_clock(
    tmp_path: Path, clock: str | None, unit: str
) -> None:
    ledger, portfolio, services = setup(tmp_path, [0.004], hours=24 * 50, clock=clock)
    result = RobustnessGate().check(portfolio, GateContext(ledger, LOCK, services))
    reason = f"only 50 common {unit}"  # 1199 hourly bars make 50 days
    assert (reason in result.reason) == (clock is not None)


def test_members_are_rerun_from_the_verified_archive(tmp_path: Path) -> None:
    ledger, portfolio, services = setup(tmp_path, [0.004])
    archive: StrategyArchive = services["archive"]
    path = archive.path(portfolio.members[0].strategy_hash)
    path.write_text(ZOO + "\n# tampered\nX = 1\n", encoding="utf-8")
    from quantcrucible.validation.archive import ArchiveError

    with pytest.raises(ArchiveError):
        RobustnessGate().check(portfolio, GateContext(ledger, LOCK, services))


def test_under_the_campaign_scope_the_stressed_dsr_counts_this_campaign(tmp_path: Path) -> None:
    """INV-125 (ADR-0050): trials and variants of another campaign enter gate ⑥′'s DSR only
    when the lock has no ``trial_scope``."""
    ledger, portfolio, services = setup(tmp_path, [0.004, 0.004])
    ledger.open_campaign("c0", "2030-01-01/2031-01-01", lock_hash="h0")
    for i in range(30):
        ledger.record_trial(
            TrialRecord(
                run_id="r", campaign_id="c0", candidate_id=f"o{i}", engine="manual", seed=0,
                strategy_hash=f"o{i}", params={}, universe="BTC/USDT", timeframe="1d",
                timerange="t", source="manual", sharpe_is=0.1 * i, returns_path="x.parquet",
                verdict="PASS",
            )
        )  # fmt: skip
    ledger.record_portfolio_variant(
        PortfolioVariant(portfolio_hash="other", campaign_id="c0", rule_config={}, members=[])
    )
    scoped = {**LOCK, "derived": {**LOCK["derived"], "trial_scope": "campaign_v1"}}
    mine = RobustnessGate().check(portfolio, GateContext(ledger, scoped, services)).detail
    legacy = RobustnessGate().check(portfolio, GateContext(ledger, LOCK, services)).detail
    assert mine is not None and legacy is not None
    assert (mine["n_trials"], mine["n_variants"]) == (2, 1)
    assert (legacy["n_trials"], legacy["n_variants"]) == (32, 2)
    assert mine["dsr_stressed_n_eff"] > legacy["dsr_stressed_n_eff"]
