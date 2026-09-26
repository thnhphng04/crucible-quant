"""The joint account replays signals, not fills (P3-20, ADR-0035) — INV-92, INV-101.

The rejected design took each member's fills from its own backtest and walked them through one
account. That cannot hold P4′: a fill's quantity was sized against the equity of the standalone
run, so on a shared account the same quantity risks a different fraction. An order sized from
100,000 replayed on an account holding 80,000 risks 1.25%, not 1% — and `SlotFill` does not even
carry the stop distance needed to re-derive it.

So the account sizes. Each slot contributes a **signal stream** — direction and stop distance per
bar, which is exactly what the `signals` sandbox job already produces — and the replay does the
sizing, the admission, the margin and the kill switch itself, against one shared balance.

The price of that is a second execution engine. `test_one_member_reproduces_the_single_strategy_
curve` is what keeps it honest: with one member and capital to spare, the replay must reproduce
the Nautilus-driven single-strategy curve. Without that test this module is arithmetic nobody
checked.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pytest

from quantcrucible.core.path_summary import segment_bar
from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.execution.engine import run_backtest
from quantcrucible.execution.joint_account import SignalReplay, SlotPlan, replay_signals
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings

BRACKETS = ({"cap": 1e9, "max_leverage": 100, "mmr": 0.004, "amount": 0.0},)
FLAT = Signal("flat", 0.0, 0.0)


def bars(
    closes: list[float], symbol: str = "A/USDT:USDT", opens: list[float] | None = None
) -> Bars:
    n = len(closes)
    day = np.datetime64("2021-01-02", "D")
    ts = (day + np.arange(n, dtype="timedelta64[D]")).astype("datetime64[ns]")
    o = np.array(opens if opens is not None else closes, dtype=np.float64)
    c = np.array(closes, dtype=np.float64)
    return Bars(symbol, "1d", ts, o, np.maximum(o, c), np.minimum(o, c), c, np.full(n, 1e9))


def bundle(series: Bars, rate: float = 0.0) -> PerpBundle:
    """Mark equal to trade, one flat intrabar path per bar, optional funding once per bar."""
    paths = tuple(
        segment_bar([0, 1], [float(series.low[k])] * 2, [float(series.high[k])] * 2, cuts=[])
        for k in range(len(series))
    )
    rows = (
        [
            [k, float(series.ts[k].astype("int64")), rate, float(series.close[k])]
            for k in range(len(series))
        ]
        if rate
        else []
    )
    return PerpBundle(
        symbol=series.symbol,
        timeframe=series.timeframe,
        marks=series,
        funding=np.array(rows, dtype=np.float64).reshape(-1, 4),
        paths=paths,
        brackets=BRACKETS,
    )


def long_for(
    n: int, stop: float = 10.0, enter_at: int = 0, exit_at: int | None = None
) -> tuple[Signal, ...]:
    out: list[Signal] = []
    for k in range(n):
        wants = k >= enter_at and (exit_at is None or k < exit_at)
        out.append(Signal("long", 1.0, stop) if wants else FLAT)
    return tuple(out)


def replay(
    plans: list[SlotPlan],
    data: dict[str, Bars],
    *,
    rate: float = 0.0,
    initial_cash: float = 100_000.0,
    leverage: int = 5,
    max_drawdown: float = 0.20,
) -> SignalReplay:
    return replay_signals(
        plans,
        data,
        {s: bundle(b, rate) for s, b in data.items()},
        initial_cash=initial_cash,
        settings=RiskSettings(max_risk_pct=0.01),
        costs=CostModel(fee_rate=0.0, slippage_bps=0.0),
        leverage=leverage,
        max_drawdown=max_drawdown,
    )


def test_a_single_slot_round_trip_is_priced_by_hand() -> None:
    """Entry at the next bar's open (ADR-0003), exit at the next open after the flat signal.

    Equity 100,000, risk 1%, stop distance 10 ⇒ 100 units. Enter at bar 1's open of 100, leave at
    bar 3's open of 110 ⇒ +1,000. No fees, no funding, so the whole move is the PnL.
    """
    price = bars([100.0, 100.0, 105.0, 110.0, 110.0])
    plan = SlotPlan("A/USDT:USDT", "long", long_for(5, stop=10.0, enter_at=0, exit_at=2))
    out = replay([plan], {"A/USDT:USDT": price})
    assert out.contributions[("A/USDT:USDT", "long")][-1] == pytest.approx(1_000.0)
    assert out.equity[-1] == pytest.approx(101_000.0)


def test_the_size_comes_from_the_shared_account_not_from_a_standalone_run() -> None:
    """The reason the design changed. The same signal on a smaller shared balance must take a
    proportionally smaller position — otherwise it is risking someone else's capital."""
    price = bars([100.0] * 5)
    plan = SlotPlan("A/USDT:USDT", "long", long_for(5, stop=10.0))
    big = replay([plan], {"A/USDT:USDT": price}, initial_cash=100_000.0)
    small = replay([plan], {"A/USDT:USDT": price}, initial_cash=80_000.0)
    assert big.opened[0].qty == pytest.approx(100.0)  # 1% of 100,000 over a stop of 10
    assert small.opened[0].qty == pytest.approx(80.0)  # 1% of 80,000 — not 100


def test_risk_at_the_stop_is_one_percent_of_the_shared_equity() -> None:
    price = bars([100.0] * 4)
    plan = SlotPlan("A/USDT:USDT", "long", long_for(4, stop=7.0))
    out = replay([plan], {"A/USDT:USDT": price}, initial_cash=50_000.0)
    opened = out.opened[0]
    assert opened.qty * 7.0 == pytest.approx(0.01 * 50_000.0)


def test_clearance_refusal_leaves_no_wallet_fee_or_risk() -> None:
    """Regression: admission used to open, charge the fee, then refuse clearance."""
    price = bars([100.0, 80.0, 80.0], opens=[100.0, 80.0, 80.0])
    plan = SlotPlan("A/USDT:USDT", "long", long_for(3, stop=10.0, exit_at=1))
    out = replay_signals(
        [plan],
        {"A/USDT:USDT": price},
        {"A/USDT:USDT": bundle(price)},
        initial_cash=100_000.0,
        settings=RiskSettings(max_risk_pct=0.01),
        costs=CostModel(fee_rate=0.01, slippage_bps=0.0),
        leverage=5,
    )

    slot = ("A/USDT:USDT", "long")
    assert out.opened == []
    assert out.denied == [(slot, "clearance")]
    np.testing.assert_allclose(out.equity, np.full(len(price), 100_000.0))
    np.testing.assert_allclose(out.contributions[slot], np.zeros(len(price)))


def test_signal_replay_uses_the_lowest_feasible_leverage_under_the_ceiling() -> None:
    price = bars([100.0] * 4)
    plan = SlotPlan("A/USDT:USDT", "long", long_for(4, stop=0.5))
    out = replay([plan], {"A/USDT:USDT": price}, initial_cash=10_000.0, leverage=10)

    assert out.opened[0].qty == pytest.approx(200.0)
    assert out.opened[0].leverage == 2


def test_every_entry_on_one_bar_shares_one_equity_snapshot() -> None:
    """INV-92. Ten slots entering together must all size from the same number, so the batch's
    total commitment is knowable before any of them is opened."""
    symbols = [f"{chr(65 + i)}/USDT:USDT" for i in range(10)]
    data = {s: bars([100.0] * 4, symbol=s) for s in symbols}
    plans = [SlotPlan(s, "long", long_for(4, stop=10.0)) for s in symbols]
    out = replay(plans, data, initial_cash=100_000.0)
    assert len(out.opened) == 10
    assert len({round(o.qty, 8) for o in out.opened}) == 1, "the slots sized from different equity"


def test_the_eleventh_slot_is_denied_with_a_reason() -> None:
    symbols = [f"{chr(65 + i)}/USDT:USDT" for i in range(11)]
    data = {s: bars([100.0] * 4, symbol=s) for s in symbols}
    plans = [SlotPlan(s, "long", long_for(4, stop=10.0)) for s in symbols]
    out = replay(plans, data, initial_cash=100_000.0)
    assert len(out.opened) == 10
    assert out.denied and out.denied[0][1] == "portfolio_risk_cap"


def test_shuffling_the_plans_changes_nothing() -> None:
    symbols = [f"{chr(65 + i)}/USDT:USDT" for i in range(11)]
    data = {s: bars([100.0] * 4, symbol=s) for s in symbols}
    plans = [SlotPlan(s, "long", long_for(4, stop=10.0)) for s in symbols]
    forward = replay(list(plans), data)
    backward = replay(list(reversed(plans)), data)
    np.testing.assert_allclose(forward.equity, backward.equity)
    assert [o.instrument for o in forward.opened] == [o.instrument for o in backward.opened]


# ── reconciliation, funding, liquidation (INV-92b) ─────────────────────────────────────


def test_the_contributions_reconcile_while_a_position_is_still_open() -> None:
    """The UI promise, in its harder form. With every position closed the sum reconciles almost by
    accident; the case that matters is a slot still holding, where the contribution has to carry
    unrealized PnL, the margin sitting outside the free balance, and the funding already paid.
    """
    price = bars([100.0, 100.0, 104.0, 108.0, 112.0])
    plan = SlotPlan("A/USDT:USDT", "long", long_for(5, stop=20.0))
    out = replay([plan], {"A/USDT:USDT": price}, rate=0.001)
    assert out.opened, "nothing was opened"
    for bar in range(len(price)):
        total = sum(curve[bar] for curve in out.contributions.values())
        assert 100_000.0 + total == pytest.approx(float(out.equity[bar]), abs=1e-6), bar


def test_both_sides_of_one_contract_are_separate_slots() -> None:
    price = bars([100.0, 100.0, 110.0, 110.0])
    long_plan = SlotPlan("A/USDT:USDT", "long", long_for(4, stop=20.0))
    short_signals = tuple(Signal("short", 1.0, 20.0) for _ in range(4))
    short_plan = SlotPlan("A/USDT:USDT", "short", short_signals)
    out = replay([long_plan, short_plan], {"A/USDT:USDT": price})
    long_c = out.contributions[("A/USDT:USDT", "long")][-1]
    short_c = out.contributions[("A/USDT:USDT", "short")][-1]
    assert long_c > 0 and short_c < 0
    assert long_c + short_c == pytest.approx(0.0, abs=1e-6)  # equal size, opposite sides


def test_long_and_short_on_one_symbol_share_the_symbol_leverage() -> None:
    price = bars([100.0] * 4)
    long_plan = SlotPlan("A/USDT:USDT", "long", long_for(4, stop=20.0))
    short_plan = SlotPlan(
        "A/USDT:USDT",
        "short",
        tuple(Signal("short", 1.0, 20.0) for _ in range(4)),
    )
    out = replay([long_plan, short_plan], {"A/USDT:USDT": price}, leverage=5)

    assert len(out.opened) == 2
    assert {(o.instrument, o.direction) for o in out.opened} == {
        ("A/USDT:USDT", "long"),
        ("A/USDT:USDT", "short"),
    }
    assert {o.leverage for o in out.opened} == {1}


def test_funding_of_both_signs_lands_on_the_right_side() -> None:
    price = bars([100.0] * 5)
    plan = SlotPlan("A/USDT:USDT", "long", long_for(5, stop=20.0))
    paid = replay([plan], {"A/USDT:USDT": price}, rate=0.001)
    received = replay([plan], {"A/USDT:USDT": price}, rate=-0.001)
    assert paid.funding_paid[("A/USDT:USDT", "long")] > 0
    assert received.funding_paid[("A/USDT:USDT", "long")] < 0
    assert paid.equity[-1] < 100_000.0 < received.equity[-1]


def test_a_liquidation_costs_the_wallet_and_nothing_more() -> None:
    """Isolated margin, at the portfolio level: the other slots must not pay for it."""
    crash = bars([100.0, 100.0, 100.0, 40.0, 40.0])
    other = bars([100.0] * 5, symbol="B/USDT:USDT")
    plans = [
        SlotPlan("A/USDT:USDT", "long", long_for(5, stop=1.0)),  # thin stop ⇒ huge position
        SlotPlan("B/USDT:USDT", "long", long_for(5, stop=20.0)),
    ]
    out = replay(plans, {"A/USDT:USDT": crash, "B/USDT:USDT": other}, leverage=20)
    assert out.liquidations or out.opened, "the fixture opened nothing"
    for bar in range(len(crash)):
        total = sum(curve[bar] for curve in out.contributions.values())
        assert 100_000.0 + total == pytest.approx(float(out.equity[bar]), abs=1e-6), bar
    assert np.all(out.equity >= 0.0)
    assert np.all(np.isfinite(out.returns))


def test_a_drawdown_breach_is_recorded_and_changes_nothing() -> None:
    """`kill_switch_drawdown` is Group A in §10.1 — it "only matters live". So the replay reports
    the breach and does not act on it: inventing a research-path rule the architecture does not
    have would change which strategies pass for reasons no decision record covers."""
    crash = bars([100.0, 100.0, 100.0, 30.0, 30.0])
    plan = SlotPlan("A/USDT:USDT", "long", long_for(5, stop=1.0))
    out = replay([plan], {"A/USDT:USDT": crash}, leverage=20, max_drawdown=0.01)
    assert out.drawdown_breach_at is not None
    assert np.all(np.isfinite(out.equity))


def test_instruments_that_do_not_share_a_timestamp_axis_are_refused() -> None:
    a = bars([100.0] * 5)
    b = bars([100.0] * 4, symbol="B/USDT:USDT")
    plans = [
        SlotPlan("A/USDT:USDT", "long", long_for(5)),
        SlotPlan("B/USDT:USDT", "long", long_for(4)),
    ]
    with pytest.raises(ValueError, match="timestamp axis"):
        replay(plans, {"A/USDT:USDT": a, "B/USDT:USDT": b})


# ── the pinning test: one member must reproduce the Nautilus-driven curve ──────────────


@dataclass
class Scripted(Strategy):
    """Replays a fixed signal list, so the bridge and the replay see literally the same stream."""

    script: tuple[Signal, ...] = ()
    seen: int = field(default=0)

    def indicators(self, series: Bars) -> Features:
        return {}

    def signal(self, x: FeatureView) -> Signal:
        sig = self.script[min(self.seen, len(self.script) - 1)]
        self.seen += 1
        return sig


def test_one_member_reproduces_the_single_strategy_curve() -> None:
    """The test that keeps this module honest.

    `replay_signals` re-implements a slice of the venue: next-open entries, the cost model, and a
    protective stop at `close ± stop_distance`. With one member and capital to spare, the two must
    agree — the shared account is not doing anything a standalone run does not.

    The comparison is on the account curve both paths report, so it is sensitive to sizing, to the
    entry and exit prices, and to fees. It runs on the *perpetual* path of `run_backtest`, whose
    equity comes from the same `PerpAccount` arithmetic.
    """
    closes = [100.0, 101.0, 103.0, 102.0, 106.0, 108.0, 107.0, 110.0, 112.0, 111.0]
    price = bars(closes)
    script = tuple(Signal("long", 1.0, 20.0) for _ in closes)  # a stop far enough never to fire
    inputs = {price.symbol: bundle(price)}

    venue = run_backtest(
        Scripted(script=script),
        {price.symbol: price},
        costs=CostModel(fee_rate=0.0, slippage_bps=0.0),
        initial_cash=100_000.0,
        lookback=2,
        risk=RiskSettings(max_risk_pct=0.01),
        perp=inputs,
        leverage=5,
    )
    replayed = replay_signals(
        [SlotPlan(price.symbol, "long", script)],
        {price.symbol: price},
        inputs,
        initial_cash=100_000.0,
        settings=RiskSettings(max_risk_pct=0.01),
        costs=CostModel(fee_rate=0.0, slippage_bps=0.0),
        leverage=5,
    )

    assert replayed.opened, "the replay opened nothing to compare"
    entry = replayed.opened[0]
    venue_entry = next(f for f in sorted(venue.fills, key=lambda f: f.ts) if f.side == "BUY")
    assert entry.qty == pytest.approx(venue_entry.qty, rel=1e-9), "the two sized differently"
    assert entry.price == pytest.approx(venue_entry.price, rel=1e-9), "different entry price"
    # Guard against a vacuous pin: two flat curves would also "agree".
    assert venue.equity.max() - venue.equity.min() > 100.0
    np.testing.assert_allclose(replayed.equity, venue.equity, rtol=1e-9)


def test_the_two_paths_agree_when_the_stop_fires() -> None:
    """The harder half: the exit price, not just the entry. A stop anchored to a different bar's
    close, or filled at a different level, shows up here and nowhere else."""
    closes = [100.0, 100.0, 100.0, 90.0, 90.0, 90.0]
    price = bars(closes)
    script = tuple(Signal("long", 1.0, 5.0) for _ in closes)  # trigger 95, reached on bar 3
    inputs = {price.symbol: bundle(price)}

    venue = run_backtest(
        Scripted(script=script),
        {price.symbol: price},
        costs=CostModel(fee_rate=0.0, slippage_bps=0.0),
        initial_cash=100_000.0,
        lookback=2,
        risk=RiskSettings(max_risk_pct=0.01),
        perp=inputs,
        leverage=5,
    )
    replayed = replay_signals(
        [SlotPlan(price.symbol, "long", script)],
        {price.symbol: price},
        inputs,
        initial_cash=100_000.0,
        settings=RiskSettings(max_risk_pct=0.01),
        costs=CostModel(fee_rate=0.0, slippage_bps=0.0),
        leverage=5,
    )
    assert [f.side for f in sorted(venue.fills, key=lambda f: f.ts)].count("SELL") >= 1
    # The bar gaps straight through the 95 trigger and opens at 90, so both paths must fill at 90
    # and lose 2,000 on a 200-unit position — not the 1,000 the trigger alone would suggest.
    assert venue.equity.max() - venue.equity.min() == pytest.approx(2_000.0)
    np.testing.assert_allclose(replayed.equity, venue.equity, rtol=1e-9)
