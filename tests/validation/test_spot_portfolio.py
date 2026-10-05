"""A spot bracket portfolio is one shared cash account (ADR-0040) — INV-113.

Before ADR-0040 a spot portfolio was the §3.2.1 weighted sum: a representative per feature-map
cell (the cell carries no instrument, so BTC-long and ETH-long in one cell eliminated each
other), a correlation filter, w ∝ 1/σ and a monthly rebalance of standalone accounts. A lock that
carries ``derived.portfolio_protocol: shared_account_v1`` replaces all of it with what the
perpetual path already does: a member per ``(instrument, direction)`` slot, replayed through one
account, every entry risking ``R`` inside the 10% cap. A lock without the tag keeps the rule it
was registered under.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pandas as pd
import pytest

from quantcrucible.core.strategy.base import FLAT, Bars, Signal
from quantcrucible.execution.exit_policy import ExitPolicy
from quantcrucible.execution.joint_account import SlotPlan
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings
from quantcrucible.execution.spot_account import replay_spot_signals
from quantcrucible.holdout import evaluator_proc
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation import research_run
from quantcrucible.validation.backtest_report import signals_stream
from quantcrucible.validation.portfolio import (
    SHARED_ACCOUNT,
    Member,
    Portfolio,
    PortfolioRule,
    consolidate_on_spot_account,
    portfolio_protocol,
    shares_spot_account,
)
from quantcrucible.validation.portfolio_dsr import PortfolioPipeline
from quantcrucible.validation.robustness import account_returns, is_account_portfolio

POLICY = ExitPolicy("bracket_timeout_v1", 1.1, 3)
COSTS = CostModel(fee_rate=0.001, slippage_bps=5.0)
SETTINGS = RiskSettings(max_risk_pct=0.01)


def lock(tag: str | None = SHARED_ACCOUNT, *, market: str = "spot", exit_: bool = True) -> Any:
    derived: dict[str, Any] = {"costs": {"fee_rate": 0.001, "slippage_bps": 5.0}}
    if exit_:
        derived["exit_protocol"] = "bracket_timeout_v1"
    if tag is not None:
        derived["portfolio_protocol"] = tag
    return {
        "research": {
            "data": {"market": market, "leverage": 5},
            "max_risk_pct": 0.01,
            "portfolio": {"max_corr": 0.5, "max_strategies": 20, "rebalance": "monthly"},
            "exit": {"tp_sl_ratio": 1.1, "max_holding_bars": 3},
        },
        "derived": derived,
    }


def daily(symbol: str, closes: list[float], start: str = "2021-01-02") -> Bars:
    n = len(closes)
    ts = (np.datetime64(start, "D") + np.arange(n, dtype="timedelta64[D]")).astype("datetime64[ns]")
    c = np.array(closes, dtype=np.float64)
    return Bars(symbol, "1d", ts, c, c * 1.01, c * 0.99, c, np.full(n, 1e9))


def entries(n: int, every: int, d: float) -> list[Signal]:
    return [Signal("long", 1.0, d, 1.1 * d) if k % every == 0 else FLAT for k in range(n)]


def write_stream(path: Path, symbol: str, signals: list[Signal]) -> Path:
    rows = [
        (symbol, k, s.direction, s.strength, s.stop_distance,
         float("nan") if s.take_profit is None else s.take_profit)
        for k, s in enumerate(signals)
    ]  # fmt: skip
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        rows, columns=["symbol", "bar", "direction", "strength", "stop_distance", "take_profit"]
    ).to_parquet(path, index=False)
    return path


def member(trial_id: int, symbol: str) -> Member:
    return Member(trial_id, f"c{trial_id}", f"h{trial_id}", {}, 0.5, (symbol,), "1d", "long")


BTC = daily("BTC/USDT", [100.0 + k for k in range(30)])
SOL = daily("SOL/USDT", [50.0 + 0.5 * k for k in range(20)], start="2021-01-12")
BTC_SIGNALS, SOL_SIGNALS = entries(30, 4, 2.0), entries(20, 3, 1.0)


def direct_replay() -> Any:
    return replay_spot_signals(
        [
            SlotPlan("BTC/USDT", "long", tuple(BTC_SIGNALS)),
            SlotPlan("SOL/USDT", "long", tuple(SOL_SIGNALS)),
        ],
        {"BTC/USDT": BTC, "SOL/USDT": SOL},
        100_000.0,
        SETTINGS,
        COSTS,
        POLICY,
    )


# ── the lock decides, and an unknown tag is refused ──────────────────────────────────────


def test_only_a_tagged_spot_bracket_lock_shares_one_cash_account() -> None:
    assert shares_spot_account(lock())
    assert not shares_spot_account(lock(None))  # registered under the weighted sum: kept
    assert not shares_spot_account(lock(market="usdt_m_perpetual"))  # the margin account
    with pytest.raises(ValueError, match="bracket_timeout_v1"):
        shares_spot_account(lock(exit_=False))
    with pytest.raises(ValueError, match="unknown locked portfolio protocol"):
        portfolio_protocol(lock("shared_account_v2"))


def test_a_studio_campaign_is_locked_with_the_shared_account(tmp_path: Path) -> None:
    """The CLI's lock is checked in ``test_run.py::test_opens_once_then_resumes``."""
    from quantcrucible.config.schema import UserConfig
    from quantcrucible.studio.service import StudioService

    service = StudioService(tmp_path, SimpleNamespace())  # type: ignore[arg-type]
    assert service._derived(UserConfig())["portfolio_protocol"] == SHARED_ACCOUNT


# ── consolidation ────────────────────────────────────────────────────────────────────────


def test_consolidation_is_the_union_axis_account_of_the_members_only(tmp_path: Path) -> None:
    streams = {
        1: write_stream(tmp_path / "1.parquet", "BTC/USDT", BTC_SIGNALS),
        2: write_stream(tmp_path / "2.parquet", "SOL/USDT", SOL_SIGNALS),
    }
    eth = daily("ETH/USDT", [10.0] * 40, start="2020-12-01")  # not a member: no say in the axis
    replay = consolidate_on_spot_account(
        (member(1, "BTC/USDT"), member(2, "SOL/USDT")),
        streams,
        {"BTC/USDT": BTC, "SOL/USDT": SOL, "ETH/USDT": eth},
        settings=SETTINGS,
        costs=COSTS,
        initial_cash=100_000.0,
        exit_policy=POLICY,
    )
    expected = direct_replay()
    assert len(replay.ts) == len(BTC)  # SOL listed later and does not cut BTC's history
    np.testing.assert_array_equal(replay.equity, expected.equity)
    assert {o.instrument for o in replay.opened} == {"BTC/USDT", "SOL/USDT"}


def test_a_spot_member_stream_on_the_wrong_side_is_refused(tmp_path: Path) -> None:
    short = [Signal("short", 1.0, 2.0, 2.2) if k == 0 else FLAT for k in range(30)]
    with pytest.raises(ValueError, match="differs from locked long"):
        consolidate_on_spot_account(
            (member(1, "BTC/USDT"),),
            {1: write_stream(tmp_path / "1.parquet", "BTC/USDT", short)},
            {"BTC/USDT": BTC},
            settings=SETTINGS,
            costs=COSTS,
            initial_cash=100_000.0,
            exit_policy=POLICY,
        )


# ── the research workflow ────────────────────────────────────────────────────────────────


def trial(tmp_path: Path, trial_id: int, symbol: str, signals: list[Signal]) -> Any:
    returns = tmp_path / "returns" / f"{trial_id}.parquet"
    write_stream(tmp_path / "signals" / f"{trial_id}.parquet", symbol, signals)
    return SimpleNamespace(
        id=trial_id, returns_path=str(returns), instrument=symbol, direction="long",
        candidate_id=f"c{trial_id}", strategy_hash=f"h{trial_id}", params={}, timeframe="1d",
    )  # fmt: skip


def session(tmp_path: Path, the_lock: Any) -> research_run.ResearchSession:
    return research_run.ResearchSession(
        Ledger.open(":memory:"),
        the_lock,
        "c1",
        {"BTC/USDT": BTC, "SOL/USDT": SOL},
        SimpleNamespace(),
        tmp_path,
    )


def test_a_tagged_spot_campaign_evaluates_one_account_per_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trials = [
        trial(tmp_path, 1, "BTC/USDT", BTC_SIGNALS),
        trial(tmp_path, 2, "SOL/USDT", SOL_SIGNALS),
    ]
    seen: dict[str, Any] = {}
    monkeypatch.setattr(research_run, "eligible_trials", lambda *_: trials)
    monkeypatch.setattr(research_run, "load_returns", lambda *_: pd.Series(dtype=float))

    def slots(chosen: Any, *args: Any, **kw: Any) -> Any:
        seen["max_strategies"] = args[-1]
        seen["clock"] = kw.get("clock")
        return chosen

    monkeypatch.setattr(research_run, "select_slots", slots)
    monkeypatch.setattr(
        research_run, "build_and_record", lambda *a, **k: pytest.fail("weighted sum used")
    )
    recorded: list[Portfolio] = []
    monkeypatch.setattr(research_run, "record_variant", lambda _, p, __: recorded.append(p))
    monkeypatch.setattr(PortfolioPipeline, "run", lambda *args: None)
    portfolio, _ = research_run.evaluate_portfolio(session(tmp_path, lock()))
    assert recorded == [portfolio]
    rule = portfolio.rule
    assert (rule.selection, rule.weighting, rule.rebalance) == ("slot_psr", "risk_per_slot", "none")
    assert seen["max_strategies"] == 20
    assert [m.universe for m in portfolio.members] == [("BTC/USDT",), ("SOL/USDT",)]
    np.testing.assert_array_equal(portfolio.returns.to_numpy(), direct_replay().returns)
    curve = pd.read_parquet(tmp_path / "account" / "c1" / f"{portfolio.portfolio_hash}.parquet")
    assert list(curve.columns[:2]) == ["ts", "equity"]
    assert {"BTC/USDT-long", "SOL/USDT-long"} <= set(curve.columns)


def test_an_untagged_spot_campaign_keeps_its_registered_weighted_sum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sentinel = Portfolio("c1", PortfolioRule(), (), pd.Series(dtype=float), 365.0)
    monkeypatch.setattr(research_run, "build_and_record", lambda *a, **k: sentinel)
    monkeypatch.setattr(research_run, "account_portfolio", lambda *a: pytest.fail("account used"))
    monkeypatch.setattr(PortfolioPipeline, "run", lambda *args: None)
    portfolio, _ = research_run.evaluate_portfolio(session(tmp_path, lock(None)))
    assert portfolio is sentinel


# ── gate ⑥′ and the holdout replay the same account ──────────────────────────────────────


class FakeRunner:
    """Answers a member's job with its fixed signals, as the sandbox or the kernels would."""

    def __init__(self, by_symbol: dict[str, list[Signal]]) -> None:
        self.by_symbol = by_symbol
        self.kinds: list[str] = []

    def run(self, job: Any) -> Any:
        self.kinds.append(job.kind)
        (symbol,) = job.bars
        stream = signals_stream({symbol: self.by_symbol[symbol]})
        result = {"signals": stream} if job.kind == "signals" else {"signals_stream": stream}
        return SimpleNamespace(ok=True, report={"result": result}, error=None)


def account_rule_portfolio() -> Portfolio:
    rule = PortfolioRule(selection="slot_psr", weighting="risk_per_slot", rebalance="none")
    members = (member(1, "BTC/USDT"), member(2, "SOL/USDT"))
    return Portfolio("c1", rule, members, pd.Series(dtype=float), 365.0)


OPTIONS = {
    "risk": {"max_risk_pct": 0.01},
    "costs": {"fee_rate": 0.001, "slippage_bps": 5.0},
    "exit_policy": {"mode": "bracket_timeout_v1", "tp_sl_ratio": 1.1, "max_holding_bars": 3},
    "initial_cash": 100_000.0,
    "max_portfolio_risk_pct": 0.10,
    "lookback": 20,
}


def test_gate_6_prime_reruns_a_spot_account_portfolio_on_the_account() -> None:
    portfolio = account_rule_portfolio()
    assert is_account_portfolio(portfolio, lock())
    runner = FakeRunner({"BTC/USDT": BTC_SIGNALS, "SOL/USDT": SOL_SIGNALS})
    archive = SimpleNamespace(get=lambda _h: "source")
    out = account_returns(
        portfolio,
        archive,  # type: ignore[arg-type]
        {"BTC/USDT": BTC, "SOL/USDT": SOL},
        OPTIONS,
        runner,
        None,
    )
    np.testing.assert_array_equal(out.to_numpy(), direct_replay().returns)
    assert runner.kinds == ["backtest", "backtest"]


def test_the_holdout_prices_a_spot_account_portfolio_from_signals() -> None:
    portfolio = account_rule_portfolio()
    assert evaluator_proc._on_one_account(portfolio.rule.as_dict())
    assert not evaluator_proc._on_one_account(PortfolioRule().as_dict())
    runner = FakeRunner({"BTC/USDT": BTC_SIGNALS, "SOL/USDT": SOL_SIGNALS})
    start = pd.Timestamp(BTC.ts[10])
    out = evaluator_proc._evaluate_spot_account(
        portfolio.members,
        {"h1": "a", "h2": "b"},
        {"BTC/USDT": BTC, "SOL/USDT": SOL},
        OPTIONS,
        runner,
        start,
    )
    expected = pd.Series(direct_replay().returns, index=pd.to_datetime(BTC.ts[1:]))
    pd.testing.assert_series_equal(out, expected[expected.index >= start])
    assert runner.kinds == ["signals", "signals"]
