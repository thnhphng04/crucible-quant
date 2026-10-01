"""One shared spot cash account across (instrument, long) slots (ADR-0040)."""

from __future__ import annotations

import numpy as np
import pytest

from quantcrucible.core.strategy.base import (
    FLAT,
    Bars,
    Features,
    FeatureView,
    Signal,
    Strategy,
    generate_signals,
)
from quantcrucible.execution.engine import run_backtest
from quantcrucible.execution.exit_policy import ExitPolicy
from quantcrucible.execution.joint_account import SlotPlan
from quantcrucible.execution.nautilus_bridge import CostModel, lot_step
from quantcrucible.execution.risk import RiskSettings
from quantcrucible.execution.spot_account import replay_spot_signals

POLICY = ExitPolicy("bracket_timeout_v1", 1.1, 5)
COSTS = CostModel()  # the default, non-zero fee and slippage
LOOKBACK = 20


class UpDay(Strategy):
    """Long after an up close, stop 3% of the close: signals that depend on price alone."""

    def indicators(self, bars: Bars) -> Features:
        return {"c": bars.close}

    def signal(self, x: FeatureView) -> Signal:
        c = x["c"]
        if c.now > c.ago(1):
            d = 0.03 * c.now
            return Signal("long", 1.0, d, 1.1 * d)
        return FLAT


def walk(symbol: str, n: int, seed: int, start: str = "2021-01-01") -> Bars:
    rng = np.random.default_rng(seed)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0.0, 0.02, n)))
    opens = np.concatenate([[100.0], close[:-1]]) * np.exp(rng.normal(0.0, 0.005, n))
    high = np.maximum(opens, close) * (1.0 + np.abs(rng.normal(0.0, 0.01, n)))
    low = np.minimum(opens, close) * (1.0 - np.abs(rng.normal(0.0, 0.01, n)))
    ts = np.datetime64(start, "ns") + np.arange(1, n + 1) * np.timedelta64(1, "D")
    return Bars(symbol, "1d", ts, opens, high, low, close, np.full(n, 1e6))


def flat(symbol: str, n: int, start: str = "2021-01-01") -> Bars:
    ts = np.datetime64(start, "ns") + np.arange(1, n + 1) * np.timedelta64(1, "D")
    one = np.full(n, 100.0)
    return Bars(symbol, "1d", ts, one, one + 1.0, one - 1.0, one.copy(), np.full(n, 1e6))


def plan(bars: Bars, strategy: Strategy | None = None) -> SlotPlan:
    signals = tuple(generate_signals(strategy or UpDay(), bars, LOOKBACK))
    return SlotPlan(bars.symbol, "long", signals)


def entry_at(n: int, bar: int, d: float) -> tuple[Signal, ...]:
    signals = [FLAT] * n
    signals[bar] = Signal("long", 1.0, d, 1.1 * d)
    return tuple(signals)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_one_slot_reproduces_the_standalone_spot_bracket_bit_for_bit(seed: int) -> None:
    """INV-112: the portfolio replay of one member is that member's own gate-③ backtest."""
    bars = walk("BTC/USDT", 300, seed)
    standalone = run_backtest(
        UpDay(), {bars.symbol: bars}, costs=COSTS, lookback=LOOKBACK, exit_policy=POLICY
    )
    replay = replay_spot_signals(
        [plan(bars)], {bars.symbol: bars}, 100_000.0, RiskSettings(), COSTS, POLICY
    )
    assert standalone.n_trades > 5
    np.testing.assert_array_equal(replay.equity, standalone.equity)
    assert [(c.bar, c.reason, c.price) for c in replay.closed] == [
        (e.bar, e.reason, e.price) for e in standalone.exits
    ]
    assert len(replay.denied) == standalone.denied_orders
    assert replay.ambiguous_bars == standalone.ambiguous_bars


def test_a_later_listed_symbol_joins_the_union_axis_without_cutting_history() -> None:
    early = walk("BTC/USDT", 200, 7)
    late = flat("SOL/USDT", 120, start="2021-03-22")  # starts 80 days later, ends together
    alone = replay_spot_signals(
        [plan(early)], {early.symbol: early}, 100_000.0, RiskSettings(), COSTS, POLICY
    )
    silent = SlotPlan(late.symbol, "long", (FLAT,) * len(late))
    both = replay_spot_signals(
        [plan(early), silent],
        {early.symbol: early, late.symbol: late},
        100_000.0,
        RiskSettings(),
        COSTS,
        POLICY,
    )
    assert len(both.ts) == len(early)  # a union, not an intersection
    np.testing.assert_array_equal(both.equity, alone.equity)


def test_a_slot_cannot_trade_before_its_instrument_has_a_bar() -> None:
    early = flat("BTC/USDT", 40)
    late = flat("SOL/USDT", 20, start="2021-01-21")
    replay = replay_spot_signals(
        [SlotPlan(late.symbol, "long", entry_at(20, 0, 5.0))],
        {early.symbol: early, late.symbol: late},
        100_000.0,
        RiskSettings(),
        COSTS,
        POLICY,
    )
    (opened,) = replay.opened
    assert opened.bar == 21  # SOL's second bar, on the union axis
    assert replay.ts[opened.bar] == late.ts[1]


def test_shared_cash_is_spent_in_canonical_order() -> None:
    a, b = flat("ETH/USDT", 10), flat("BTC/USDT", 10)
    # d = 0.1 ⇒ R/d = 100 units per slot, far more than 1,000 of cash buys: cash binds
    plans = [SlotPlan(s.symbol, "long", entry_at(10, 2, 0.1)) for s in (a, b)]
    replay = replay_spot_signals(
        plans, {a.symbol: a, b.symbol: b}, 1_000.0, RiskSettings(), COSTS, POLICY
    )
    assert [o.instrument for o in replay.opened] == ["BTC/USDT"]  # instrument ascending
    assert replay.opened[0].qty * 100.0 <= 1_000.0
    assert replay.denied == [(("ETH/USDT", "long"), "cash")]


def test_the_portfolio_cap_denies_an_entry_past_it() -> None:
    a, b = flat("ETH/USDT", 10), flat("BTC/USDT", 10)
    plans = [SlotPlan(s.symbol, "long", entry_at(10, 2, 5.0)) for s in (a, b)]
    replay = replay_spot_signals(
        plans,
        {a.symbol: a, b.symbol: b},
        100_000.0,
        RiskSettings(),
        COSTS,
        POLICY,
        max_portfolio_risk_pct=0.015,  # room for one 1% entry, not two
    )
    assert [o.instrument for o in replay.opened] == ["BTC/USDT"]
    assert replay.denied == [(("ETH/USDT", "long"), "portfolio_risk_cap")]


def test_every_entry_of_a_bar_sizes_from_one_equity_snapshot() -> None:
    a, b = flat("ETH/USDT", 10), flat("BTC/USDT", 10)
    plans = [SlotPlan(s.symbol, "long", entry_at(10, 2, 5.0)) for s in (a, b)]
    replay = replay_spot_signals(
        plans, {a.symbol: a, b.symbol: b}, 100_000.0, RiskSettings(), COSTS, POLICY
    )
    # both from the bar-2 close equity: the first purchase's fee does not shrink the second
    (btc, eth) = replay.opened
    assert btc.qty == eth.qty
    risk = 100_000.0 * 0.01
    assert btc.qty * (btc.price - btc.stop) <= risk
    assert btc.qty * (btc.price - btc.stop) > risk - lot_step("BTC/USDT") * 5.0 - 1e-9


def test_contributions_decompose_account_equity() -> None:
    a, b = walk("ETH/USDT", 250, 11), walk("BTC/USDT", 250, 12)
    replay = replay_spot_signals(
        [plan(a), plan(b)], {a.symbol: a, b.symbol: b}, 100_000.0, RiskSettings(), COSTS, POLICY
    )
    assert len(replay.opened) > 10
    total = 100_000.0 + sum(replay.contributions.values())
    np.testing.assert_allclose(total, replay.equity, rtol=0, atol=1e-6)


def test_short_slots_and_legacy_exits_are_refused() -> None:
    bars = flat("BTC/USDT", 5)
    with pytest.raises(ValueError, match="long only"):
        replay_spot_signals(
            [SlotPlan(bars.symbol, "short", (FLAT,) * 5)],
            {bars.symbol: bars},
            100_000.0,
            RiskSettings(),
            COSTS,
            POLICY,
        )
    with pytest.raises(ValueError, match="bracket_timeout_v1"):
        replay_spot_signals(
            [plan(bars)], {bars.symbol: bars}, 100_000.0, RiskSettings(), COSTS, ExitPolicy()
        )


def test_one_strategy_per_slot_and_matching_lengths() -> None:
    bars = flat("BTC/USDT", 5)
    with pytest.raises(ValueError, match="duplicate slot"):
        replay_spot_signals(
            [plan(bars), plan(bars)], {bars.symbol: bars}, 1e5, RiskSettings(), COSTS, POLICY
        )
    with pytest.raises(ValueError, match="length"):
        replay_spot_signals(
            [SlotPlan(bars.symbol, "long", (FLAT,) * 4)],
            {bars.symbol: bars},
            1e5,
            RiskSettings(),
            COSTS,
            POLICY,
        )
