"""Locked bracket exits: price levels, time expiry, and one-slot account parity."""

from __future__ import annotations

import numpy as np
import pytest

from quantcrucible.core.path_summary import segment_bar
from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import FLAT, Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.execution.engine import run_backtest
from quantcrucible.execution.exit_policy import Bracket, ExitPolicy, resolve_ohlc, resolve_paths
from quantcrucible.execution.joint_account import SlotPlan, replay_signals
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings

POLICY = ExitPolicy("bracket_timeout_v1", 1.1, 2)
ZERO_COST = CostModel(fee_rate=0.0, slippage_bps=0.0)
BRACKETS = ({"cap": 1e9, "max_leverage": 100, "mmr": 0.004, "amount": 0.0},)


class Once(Strategy):
    def indicators(self, bars: Bars) -> Features:
        return {"bar": np.arange(len(bars), dtype=np.float64)}

    def signal(self, x: FeatureView) -> Signal:
        return Signal("long", 1.0, 10.0, 11.0) if float(x["bar"]) == 0 else FLAT


def prices(
    opens: list[float],
    highs: list[float],
    lows: list[float],
    symbol: str = "TEST/USDT",
) -> Bars:
    n = len(opens)
    ts = np.datetime64("2021-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "D")
    return Bars(
        symbol,
        "1d",
        ts,
        np.array(opens, dtype=float),
        np.array(highs, dtype=float),
        np.array(lows, dtype=float),
        np.array(opens, dtype=float),
        np.full(n, 1e6),
    )


def bundle(bars: Bars) -> PerpBundle:
    paths = tuple(
        segment_bar([0, 1], [float(bars.low[i])] * 2, [float(bars.high[i])] * 2, [])
        for i in range(len(bars))
    )
    return PerpBundle(
        bars.symbol,
        bars.timeframe,
        bars,
        np.zeros((0, 4)),
        paths,
        BRACKETS,
        trade_paths=paths,
    )


def test_spot_tp_and_flat_does_not_close_the_position() -> None:
    bars = prices([100, 100, 100, 100], [101, 105, 112, 101], [99, 95, 95, 99])
    out = run_backtest(Once(), {bars.symbol: bars}, costs=ZERO_COST, exit_policy=POLICY)
    assert [(e.bar, e.reason, e.price) for e in out.exits] == [(2, "take_profit", 111.0)]
    assert out.n_trades == 1
    assert out.stops_placed[0].trigger == 90.0


def test_spot_ambiguity_is_a_stop_and_a_gap_fills_worse() -> None:
    bars = prices([100, 100, 100], [101, 113, 101], [99, 89, 99])
    out = run_backtest(Once(), {bars.symbol: bars}, costs=ZERO_COST, exit_policy=POLICY)
    assert [(e.reason, e.price) for e in out.exits] == [("stop", 90.0)]
    assert out.ambiguous_bars == 1
    gap = prices([100, 85, 100], [101, 113, 101], [99, 80, 99])
    rejected = run_backtest(Once(), {gap.symbol: gap}, costs=ZERO_COST, exit_policy=POLICY)
    assert rejected.denied_orders == 1  # opening beyond the stop must not enter
    held_gap = prices([100, 100, 80, 100], [101, 105, 85, 101], [99, 95, 75, 99])
    exited = run_backtest(Once(), {held_gap.symbol: held_gap}, costs=ZERO_COST, exit_policy=POLICY)
    assert [(e.reason, e.price) for e in exited.exits] == [("stop", 80.0)]


def test_time_exit_after_two_complete_held_bars() -> None:
    bars = prices([100, 100, 100, 105, 105], [101, 105, 105, 106, 106], [99, 95, 95, 104, 104])
    out = run_backtest(Once(), {bars.symbol: bars}, costs=ZERO_COST, exit_policy=POLICY)
    assert [(e.bar, e.reason, e.price) for e in out.exits] == [(3, "timeout", 105.0)]
    assert out.avg_holding_bars == 2


def test_default_hundred_bar_limit_exits_at_bar_101_open() -> None:
    bars = prices([100.0] * 103, [101.0] * 103, [99.0] * 103)
    out = run_backtest(
        Once(),
        {bars.symbol: bars},
        costs=ZERO_COST,
        exit_policy=ExitPolicy("bracket_timeout_v1", 1.1, 100),
    )
    assert [(e.bar, e.reason) for e in out.exits] == [(101, "timeout")]
    assert out.avg_holding_bars == 100


def test_wrong_tp_ratio_is_refused() -> None:
    class Wrong(Once):
        def signal(self, x: FeatureView) -> Signal:
            return Signal("long", 1.0, 10.0, 8.0) if float(x["bar"]) == 0 else FLAT

    bars = prices([100, 100], [101, 101], [99, 99])
    with pytest.raises(ValueError, match="locked TP/SL ratio"):
        run_backtest(Wrong(), {bars.symbol: bars}, costs=ZERO_COST, exit_policy=POLICY)


def test_short_levels_and_strict_tp_trade_through() -> None:
    bracket = Bracket("short", 110.0, 89.0)
    assert resolve_ohlc(bracket, 100.0, 109.0, 89.0) == (None, False)
    assert resolve_ohlc(bracket, 100.0, 109.0, 88.0) == ("take_profit", False)


def test_lock_without_exit_version_is_legacy_and_unknown_version_is_refused() -> None:
    assert ExitPolicy.from_lock({"derived": {}, "research": {}}).mode == "legacy_flat"
    assert ExitPolicy.from_lock(
        {
            "derived": {"exit_protocol": "bracket_timeout_v1"},
            "research": {"exit": {"tp_sl_ratio": 1.1, "max_holding_bars": 100}},
        }
    ) == ExitPolicy("bracket_timeout_v1", 1.1, 100)
    with pytest.raises(ValueError, match="unknown locked exit protocol"):
        ExitPolicy.from_lock({"derived": {"exit_protocol": "v2"}})


def test_one_perpetual_slot_reproduces_standalone_bracket_equity() -> None:
    bars = prices(
        [100, 100, 100, 100],
        [101, 105, 112, 101],
        [99, 95, 95, 99],
        "TEST/USDT:USDT",
    )
    inputs = {bars.symbol: bundle(bars)}
    standalone = run_backtest(
        Once(), {bars.symbol: bars}, costs=ZERO_COST, perp=inputs, exit_policy=POLICY
    )
    replayed = replay_signals(
        [SlotPlan(bars.symbol, "long", (Signal("long", 1.0, 10.0, 11.0), FLAT, FLAT, FLAT))],
        {bars.symbol: bars},
        inputs,
        100_000.0,
        RiskSettings(),
        ZERO_COST,
        exit_policy=POLICY,
    )
    np.testing.assert_allclose(standalone.equity, replayed.equity, rtol=1e-9)
    assert [(e.reason, e.price) for e in standalone.exits] == [("take_profit", 111.0)]


def test_mark_only_tp_touch_does_not_close_trade_position() -> None:
    bars = prices(
        [100, 100, 100, 100],
        [101, 105, 105, 101],
        [99, 95, 95, 99],
        "TEST/USDT:USDT",
    )
    original = bundle(bars)
    mark = tuple(
        segment_bar([0, 1], [95.0, 95.0], [112.0, 112.0], []) if i == 2 else original.paths[i]
        for i in range(len(bars))
    )
    inputs = {
        bars.symbol: PerpBundle(
            bars.symbol,
            bars.timeframe,
            bars,
            original.funding,
            mark,
            BRACKETS,
            trade_paths=original.trade_paths,
        )
    }
    result = run_backtest(
        Once(), {bars.symbol: bars}, costs=ZERO_COST, perp=inputs, exit_policy=POLICY
    )
    assert [e.reason for e in result.exits] == ["timeout"]


def test_funding_after_take_profit_is_not_charged() -> None:
    bars = prices(
        [100, 100, 100, 100],
        [101, 105, 112, 101],
        [99, 95, 95, 99],
        "TEST/USDT:USDT",
    )
    original = bundle(bars)
    assert original.trade_paths is not None
    minute = list(range(120))
    trade_high = [112.0 if i == 10 else 100.0 for i in minute]
    mark_segmented = segment_bar(minute, [100.0] * 120, [100.0] * 120, [60])
    trade_segmented = segment_bar(minute, [100.0] * 120, trade_high, [60])
    paths = (*original.paths[:2], mark_segmented, original.paths[3])
    trade_paths = (*original.trade_paths[:2], trade_segmented, original.trade_paths[3])
    funding_ts = int(bars.ts[2].astype("int64")) - 1_440 * 60_000_000_000 + 60 * 60_000_000_000
    inputs = {
        bars.symbol: PerpBundle(
            bars.symbol,
            bars.timeframe,
            bars,
            np.array([[2, funding_ts, 0.01, 100.0]], dtype=float),
            paths,
            BRACKETS,
            trade_paths=trade_paths,
        )
    }
    out = run_backtest(
        Once(), {bars.symbol: bars}, costs=ZERO_COST, perp=inputs, exit_policy=POLICY
    )
    assert [e.reason for e in out.exits] == ["take_profit"]
    assert out.funding_paid == 0


def test_same_minute_liquidation_beats_stop_and_tp() -> None:
    path = segment_bar([0, 1], [89.0, 89.0], [112.0, 112.0], [])
    reason, minute, ambiguous = resolve_paths(Bracket("long", 90.0, 111.0), path, path, [95.0])
    assert (reason, minute, ambiguous) == ("liquidation", 0, True)


def test_short_perpetual_take_profit_uses_the_lower_trade_path() -> None:
    bars = prices(
        [100, 100, 100, 100],
        [101, 105, 105, 101],
        [99, 95, 88, 99],
        "TEST/USDT:USDT",
    )
    plan = SlotPlan(bars.symbol, "short", (Signal("short", 1.0, 10.0, 11.0), FLAT, FLAT, FLAT))
    out = replay_signals(
        [plan],
        {bars.symbol: bars},
        {bars.symbol: bundle(bars)},
        100_000.0,
        RiskSettings(),
        ZERO_COST,
        exit_policy=POLICY,
    )
    assert [(e.reason, e.price) for e in out.closed] == [("take_profit", 89.0)]


def test_funding_is_charged_once_per_hedged_wallet() -> None:
    bars = prices(
        [100, 100, 100, 100],
        [101, 105, 105, 101],
        [99, 95, 95, 99],
        "TEST/USDT:USDT",
    )
    original = bundle(bars)
    assert original.trade_paths is not None
    minute = list(range(120))
    flat = segment_bar(minute, [100.0] * 120, [100.0] * 120, [60])
    paths = (*original.paths[:2], flat, original.paths[3])
    ts = int(bars.ts[2].astype("int64")) - 1_440 * 60_000_000_000 + 60 * 60_000_000_000
    inp = PerpBundle(
        bars.symbol,
        bars.timeframe,
        bars,
        np.array([[2, ts, 0.01, 100.0]]),
        paths,
        BRACKETS,
        trade_paths=paths,
    )
    plans = [
        SlotPlan(bars.symbol, "long", (Signal("long", 1.0, 10.0, 11.0), FLAT, FLAT, FLAT)),
        SlotPlan(bars.symbol, "short", (Signal("short", 1.0, 10.0, 11.0), FLAT, FLAT, FLAT)),
    ]
    out = replay_signals(
        plans,
        {bars.symbol: bars},
        {bars.symbol: inp},
        100_000.0,
        RiskSettings(),
        ZERO_COST,
        exit_policy=ExitPolicy("bracket_timeout_v1", 1.1, 10),
    )
    assert out.funding_paid[(bars.symbol, "long")] == pytest.approx(100.0)
    assert out.funding_paid[(bars.symbol, "short")] == pytest.approx(-100.0)
