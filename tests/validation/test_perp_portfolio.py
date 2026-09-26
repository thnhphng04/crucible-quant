"""The perpetual portfolio is one account replay, not a weighted sum (P3-21, ADR-0035) — INV-102.

`combine(frame, weights, rebalance)` describes N self-financed return streams blended by weight.
That stops describing the real thing the moment ten strategies share one USDT balance: their
positions compete for the same margin, the same 10% risk cap and the same ten slots. And under
`Q = R/d` there is nothing left for naive risk parity to weight — every slot risks exactly 1%.

So on the perpetual path the consolidated series is the shared account's return, produced by
replaying the members' **signal streams** through one account. The weighted sum is kept for the
spot path, where it is what every existing result was measured under.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from quantcrucible.core.path_summary import segment_bar
from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars
from quantcrucible.execution.joint_account import SignalReplay
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation import research_run
from quantcrucible.validation.portfolio import (
    Member,
    Portfolio,
    PortfolioRule,
    consolidate_on_account,
    load_signal_stream,
)
from quantcrucible.validation.portfolio_dsr import PortfolioPipeline

BRACKETS = ({"cap": 1e9, "max_leverage": 100, "mmr": 0.004, "amount": 0.0},)
RULE = PortfolioRule(max_corr=0.5, max_strategies=20, rebalance="monthly")


def bars(closes: list[float], symbol: str) -> Bars:
    n = len(closes)
    day = np.datetime64("2021-01-02", "D")
    ts = (day + np.arange(n, dtype="timedelta64[D]")).astype("datetime64[ns]")
    c = np.array(closes, dtype=np.float64)
    return Bars(symbol, "1d", ts, c, c, c, c, np.full(n, 1e9))


def bundle(series: Bars) -> PerpBundle:
    return PerpBundle(
        symbol=series.symbol,
        timeframe=series.timeframe,
        marks=series,
        funding=np.zeros((0, 4), dtype=np.float64),
        paths=tuple(
            segment_bar([0, 1], [float(series.low[k])] * 2, [float(series.high[k])] * 2, cuts=[])
            for k in range(len(series))
        ),
        brackets=BRACKETS,
    )


def write_stream(path: Path, symbol: str, n: int, stop: float) -> Path:
    rows = [(symbol, k, "long", 1.0, stop, float("nan")) for k in range(n)]
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        rows, columns=["symbol", "bar", "direction", "strength", "stop_distance", "take_profit"]
    ).to_parquet(path, index=False)
    return path


def member(trial_id: int, symbol: str) -> Member:
    return Member(trial_id, f"c{trial_id}", f"h{trial_id}", {}, 0.5, (symbol,), "1d", "long")


def test_a_signal_stream_round_trips_from_its_parquet(tmp_path: Path) -> None:
    path = write_stream(tmp_path / "s" / "x.parquet", "A/USDT:USDT", 4, 10.0)
    plans = load_signal_stream(path)
    assert [p.instrument for p in plans] == ["A/USDT:USDT"]
    assert len(plans[0].signals) == 4
    assert plans[0].signals[0].stop_distance == pytest.approx(10.0)


def test_the_consolidated_series_is_the_shared_account(tmp_path: Path) -> None:
    """Two members, one balance. The account's return is not the weighted average of what each
    would have made alone, because the second position is sized from an equity the first has
    already committed margin out of."""
    a = bars([100.0, 100.0, 104.0, 108.0], "A/USDT:USDT")
    b = bars([100.0, 100.0, 102.0, 101.0], "B/USDT:USDT")
    streams = {
        1: write_stream(tmp_path / "sig" / "1.parquet", "A/USDT:USDT", 4, 10.0),
        2: write_stream(tmp_path / "sig" / "2.parquet", "B/USDT:USDT", 4, 10.0),
    }
    members = (member(1, "A/USDT:USDT"), member(2, "B/USDT:USDT"))
    replay = consolidate_on_account(
        members,
        streams,
        {"A/USDT:USDT": a, "B/USDT:USDT": b},
        {"A/USDT:USDT": bundle(a), "B/USDT:USDT": bundle(b)},
        settings=RiskSettings(max_risk_pct=0.01),
        costs=CostModel(fee_rate=0.0, slippage_bps=0.0),
        initial_cash=100_000.0,
        leverage=5,
    )
    assert len(replay.opened) == 2
    # both sized from the same snapshot — that is the shared-account property (INV-92)
    assert len({round(o.qty, 8) for o in replay.opened}) == 1
    # and the decomposition holds at every bar, including with positions open
    for k in range(len(a)):
        total = sum(curve[k] for curve in replay.contributions.values())
        assert 100_000.0 + total == pytest.approx(float(replay.equity[k]), abs=1e-6)


def test_a_portfolio_can_be_rebuilt_on_the_replays_returns(tmp_path: Path) -> None:
    """The Portfolio object carries the account's series, so gate ⑤'s DSR is computed on what the
    account actually did rather than on a blend of what the members did apart."""
    a = bars([100.0, 100.0, 104.0, 108.0, 112.0], "A/USDT:USDT")
    streams = {1: write_stream(tmp_path / "sig" / "1.parquet", "A/USDT:USDT", 5, 10.0)}
    members = (member(1, "A/USDT:USDT"),)
    replay = consolidate_on_account(
        members,
        streams,
        {"A/USDT:USDT": a},
        {"A/USDT:USDT": bundle(a)},
        settings=RiskSettings(max_risk_pct=0.01),
        costs=CostModel(fee_rate=0.0, slippage_bps=0.0),
        initial_cash=100_000.0,
        leverage=5,
    )
    series = pd.Series(replay.returns, index=pd.to_datetime(replay.ts[1:]))
    portfolio = Portfolio("c1", RULE, members, series, 365.0)
    assert np.isfinite(portfolio.sharpe_is)
    assert len(portfolio.returns) == len(a) - 1


def test_a_member_whose_stream_names_another_instrument_is_refused(tmp_path: Path) -> None:
    """A stream is matched to its member by instrument, not by position in a list: a silent
    mismatch would replay one strategy's signals against another's market."""
    path = write_stream(tmp_path / "sig" / "1.parquet", "Z/USDT:USDT", 4, 10.0)
    a = bars([100.0] * 4, "A/USDT:USDT")
    with pytest.raises(ValueError, match="Z/USDT:USDT"):
        consolidate_on_account(
            (member(1, "A/USDT:USDT"),),
            {1: path},
            {"A/USDT:USDT": a},
            {"A/USDT:USDT": bundle(a)},
            settings=RiskSettings(max_risk_pct=0.01),
            costs=CostModel(fee_rate=0.0, slippage_bps=0.0),
            initial_cash=100_000.0,
            leverage=5,
        )


def test_duplicate_slot_or_truncated_stream_is_refused(tmp_path: Path) -> None:
    symbol = "A/USDT:USDT"
    trade = bars([100.0] * 4, symbol)
    first = write_stream(tmp_path / "sig" / "1.parquet", symbol, 4, 10.0)
    second = write_stream(tmp_path / "sig" / "2.parquet", symbol, 4, 10.0)
    settings = RiskSettings(max_risk_pct=0.01)
    costs = CostModel(fee_rate=0.0, slippage_bps=0.0)
    with pytest.raises(ValueError, match="duplicate perpetual slot"):
        consolidate_on_account(
            (member(1, symbol), member(2, symbol)),
            {1: first, 2: second},
            {symbol: trade},
            {symbol: bundle(trade)},
            settings=settings,
            costs=costs,
            initial_cash=100_000.0,
            leverage=5,
        )
    short = write_stream(tmp_path / "sig" / "3.parquet", symbol, 3, 10.0)
    with pytest.raises(ValueError, match="signal stream length"):
        consolidate_on_account(
            (member(3, symbol),),
            {3: short},
            {symbol: trade},
            {symbol: bundle(trade)},
            settings=settings,
            costs=costs,
            initial_cash=100_000.0,
            leverage=5,
        )


def test_signal_direction_must_match_locked_member(tmp_path: Path) -> None:
    symbol = "A/USDT:USDT"
    trade = bars([100.0] * 4, symbol)
    long_stream = write_stream(tmp_path / "sig" / "1.parquet", symbol, 4, 10.0)
    wrong_member = Member(1, "c1", "h1", {}, 1.0, (symbol,), "1d", "short")
    with pytest.raises(ValueError, match="signal direction long differs from locked short"):
        consolidate_on_account(
            (wrong_member,),
            {1: long_stream},
            {symbol: trade},
            {symbol: bundle(trade)},
            settings=RiskSettings(max_risk_pct=0.01),
            costs=CostModel(fee_rate=0.0, slippage_bps=0.0),
            initial_cash=100_000.0,
            leverage=5,
        )


def test_research_pipeline_records_the_account_curve_not_a_weighted_blend(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    symbol = "A/USDT:USDT"
    trade = bars([100.0, 101.0, 102.0, 103.0], symbol)
    selected = Portfolio(
        "c1",
        RULE,
        (member(1, symbol),),
        pd.Series([0.9, 0.9, 0.9], index=pd.to_datetime(trade.ts[1:])),
        365.0,
    )
    replay = SignalReplay(
        ts=trade.ts,
        equity=np.array([100_000.0, 100_000.0, 100_100.0, 100_200.0]),
        contributions={(symbol, "long"): np.array([0.0, 0.0, 100.0, 200.0])},
    )
    recorded: list[Portfolio] = []
    curves: list[str] = []
    trial = SimpleNamespace(
        id=1,
        returns_path=str(tmp_path / "returns" / "1.parquet"),
        instrument=symbol,
        direction="long",
        candidate_id="c1",
        strategy_hash="h1",
        params={},
        timeframe="1d",
    )
    monkeypatch.setattr(
        research_run,
        "eligible_trials",
        lambda *_: [trial],
    )
    monkeypatch.setattr(research_run, "load_returns", lambda *_: selected.returns)
    monkeypatch.setattr(research_run, "select_slots", lambda *args: [trial])
    monkeypatch.setattr(
        research_run, "load_signal_stream", lambda *_: [SimpleNamespace(slot=(symbol, "long"))]
    )
    monkeypatch.setattr(research_run, "consolidate_on_account", lambda *args, **kw: replay)
    monkeypatch.setattr(research_run, "record_variant", lambda _, p, __: recorded.append(p))
    monkeypatch.setattr(
        research_run,
        "write_account_curve",
        lambda _, __, portfolio_hash, **kw: curves.append(portfolio_hash),
    )
    monkeypatch.setattr(PortfolioPipeline, "run", lambda *args: None)
    lock = {
        "research": {
            "data": {"market": "usdt_m_perpetual", "leverage": 5},
            "max_risk_pct": 0.01,
            "portfolio": {"max_corr": 0.5, "max_strategies": 20, "rebalance": "monthly"},
        },
        "derived": {"costs": {"fee_rate": 0.0, "slippage_bps": 0.0}},
    }
    session = research_run.ResearchSession(
        Ledger.open(":memory:"),
        lock,
        "c1",
        {symbol: trade},
        SimpleNamespace(),
        tmp_path,
        perp_data={symbol: bundle(trade)},
    )
    portfolio, _ = research_run.evaluate_portfolio(session)
    assert portfolio.rule.weighting == "risk_per_slot"
    assert portfolio.rule.selection == "slot_psr"
    assert portfolio.rule.rebalance == "none"
    np.testing.assert_allclose(portfolio.returns.to_numpy(), replay.returns)
    assert recorded == [portfolio]
    assert curves == [portfolio.portfolio_hash]
    assert session.context().services["perp_data"][symbol] is session.perp_data[symbol]
