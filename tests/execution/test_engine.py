"""Backtest engine (P0-10, INV-35): next-open fills, trade-through limits, determinism."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.enums import OrderSide, TimeInForce
from nautilus_trader.trading.strategy import Strategy as NautilusStrategy

from quantcrucible.core.strategy.base import FLAT, Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.core.zoo.ema_crossover import GeneratedStrategy
from quantcrucible.execution.engine import BacktestAbortedError, BacktestResult, run_backtest
from quantcrucible.execution.nautilus_bridge import CostModel, bar_type_for, build_engine
from tests.factories import make_bars

DAY_NS = 86_400 * 10**9
NO_STOP = 1e9  # stop distance larger than any price: no protective stop is placed


def ladder(opens: list[float], wick: float = 5.0, up: float = 2.0) -> Bars:
    """Bars with open o, high o+wick, low o-wick, close o+up — easy to reason about by hand."""
    o = np.asarray(opens, dtype=np.float64)
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, len(o) + 1) * np.timedelta64(1, "D")
    return Bars("TEST/USDT", "1d", ts, o, o + wick, o - wick, o + up, np.full(len(o), 1_000.0))


class LongFrom(Strategy):
    """Long from bar ``start`` (0-based, counted in bars seen) until bar ``stop``."""

    def __init__(self, start: int, stop: int = 10**9, stop_distance: float = NO_STOP) -> None:
        super().__init__()
        self.start, self.stop, self.stop_distance = start, stop, stop_distance

    def indicators(self, bars: Bars) -> Features:
        return {"n": np.arange(len(bars), dtype=np.float64)}

    def signal(self, x: FeatureView) -> Signal:
        if self.start <= float(x["n"]) < self.stop:
            return Signal("long", 1.0, self.stop_distance)
        return FLAT


class AlwaysShort(Strategy):
    def indicators(self, bars: Bars) -> Features:
        return {}

    def signal(self, x: FeatureView) -> Signal:
        return Signal("short", 1.0, 5.0)


@dataclass(frozen=True)
class FixedNotional:
    """Test-only sizer: a fixed notional per instrument × strength. The execution mechanics
    tested here (next-open fills, stops, costs) do not depend on the Risk layer, which has its
    own tests (tests/core/sizing, tests/execution/test_risk.py)."""

    notional: float

    def target(self, symbol: str, signal: Signal, window: Bars, equity: float) -> float:
        return self.notional * signal.strength / float(window.close[-1])


def bt(
    strategy: Strategy, bars_by_symbol: dict[str, Bars], gross: float = 0.5, **kw: Any
) -> BacktestResult:
    """run_backtest with a fixed notional of ``gross`` × cash, split across the symbols."""
    cash = float(kw.get("initial_cash", 100_000.0))
    sizer = FixedNotional(cash * gross / len(bars_by_symbol))
    return run_backtest(strategy, bars_by_symbol, sizer=sizer, **kw)


OPENS = [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0]


def test_signal_on_close_fills_at_next_open() -> None:
    bars = ladder(OPENS)
    res = bt(LongFrom(0), {"TEST/USDT": bars}, lookback=len(bars))
    fill = res.fills[0]
    assert fill.side == "BUY"
    assert fill.price == pytest.approx(OPENS[1])  # not bar 0's close (102), not bar 1's close
    t0, t1 = (int(x) for x in bars.ts[:2].astype(np.int64))
    assert t0 < fill.ts < t1


def test_next_bar_execution_entry_and_exit() -> None:
    bars = ladder(OPENS)
    res = bt(LongFrom(3, 6), {"TEST/USDT": bars}, lookback=len(bars))
    assert [(f.side, f.price) for f in res.fills] == [("BUY", OPENS[4]), ("SELL", OPENS[7])]
    assert res.n_trades == 1
    assert res.avg_holding_bars == 3


def test_golden_equity_and_costs() -> None:
    opens = OPENS[:4]  # the target drifts < 25% here, so the position is never resized
    bars = ladder(opens)
    costs = CostModel(fee_rate=0.001, slippage_bps=5.0)
    res = bt(LongFrom(0), {"TEST/USDT": bars}, costs=costs, initial_cash=100_000)
    qty = 50_000 / 102.0  # FixedNotional: 50% of cash / close of the signal bar
    (fill,) = res.fills
    assert fill.qty == pytest.approx(qty, rel=1e-6)
    fee = qty * 110.0 * 0.0015
    assert fill.commission == pytest.approx(fee, rel=1e-6)
    cash = 100_000 - qty * 110.0 - fee
    expected = np.array([100_000.0] + [cash + qty * (o + 2) for o in opens[1:]])
    np.testing.assert_allclose(res.equity, expected, rtol=1e-9)
    assert len(res.returns) == len(bars) - 1
    assert res.total_return == pytest.approx(expected[-1] / 100_000 - 1)
    assert res.signals == {"long": len(bars), "short": 0, "flat": 0}


def test_higher_costs_lower_return() -> None:
    bars = make_bars(300, seed=3, drift=0.001)
    strat = GeneratedStrategy({"fast": 5, "slow": 40, "k_atr": 2.0})
    base = run_backtest(strat, {"TEST/USDT": bars})
    assert base.n_trades > 0
    doubled = run_backtest(strat, {"TEST/USDT": bars}, costs=CostModel().scaled(2.0, 2.0))
    assert doubled.total_return < base.total_return


def test_deterministic() -> None:
    bars = make_bars(250, seed=7)
    strat = GeneratedStrategy({"fast": 10, "slow": 50, "k_atr": 2.0})
    a = run_backtest(strat, {"TEST/USDT": bars}, seed=1)
    b = run_backtest(strat, {"TEST/USDT": bars}, seed=1)
    np.testing.assert_array_equal(a.equity, b.equity)
    assert a.fills == b.fills


def test_protective_stop_fills_at_gap_open() -> None:
    bars = ladder([100.0, 100.0, 100.0, 80.0, 80.0, 80.0], wick=1.0, up=0.0)
    # entry at the open of bar 1; the stop (close - 3 = 97) is re-issued every bar; bar 3 gaps
    # down to 80, so the stop fills there — at the gap, not at its trigger price
    res = bt(LongFrom(0, stop_distance=3.0), {"TEST/USDT": bars})
    assert [(f.side, f.price) for f in res.fills][:2] == [("BUY", 100.0), ("SELL", 80.0)]
    assert res.n_trades >= 1


def test_short_signal_means_flat_on_spot() -> None:
    bars = ladder(OPENS)
    res = bt(AlwaysShort(), {"TEST/USDT": bars})
    assert res.fills == ()
    assert res.signals["short"] == len(bars)
    np.testing.assert_allclose(res.equity, 100_000.0)


def test_multi_symbol() -> None:
    a = make_bars(120, seed=1, symbol="AAA/USDT")
    b = make_bars(120, seed=2, symbol="BBB/USDT")
    res = bt(LongFrom(0), {"AAA/USDT": a, "BBB/USDT": b})
    assert {f.symbol for f in res.fills} == {"AAA/USDT", "BBB/USDT"}
    assert res.denied_orders == 0
    assert len(res.equity) == 120


def test_engine_stop_is_not_a_silent_truncation() -> None:
    with pytest.raises(BacktestAbortedError):  # all cash at the close, filled at a higher open
        bt(LongFrom(0), {"TEST/USDT": ladder(OPENS)}, gross=1.0)


def test_mixed_timeframes_rejected() -> None:
    a = make_bars(10)
    b = Bars("B/USDT", "4h", a.ts, a.open, a.high, a.low, a.close, a.volume)
    with pytest.raises(ValueError, match="mixed timeframes"):
        bt(LongFrom(0), {"A/USDT": a, "B/USDT": b})


class _LimitCfg(StrategyConfig, frozen=True):
    pass


class _LimitBuyer(NautilusStrategy):  # type: ignore[misc]
    """Places one GTC limit buy after the second bar, then records fills."""

    def __init__(self, instrument: Any, bar_type: Any, price: float) -> None:
        super().__init__(_LimitCfg())
        self.inst, self.bt, self.px = instrument, bar_type, price
        self.n = 0
        self.fills: list[float] = []

    def on_start(self) -> None:
        self.subscribe_bars(self.bt)

    def on_bar(self, bar: Bar) -> None:
        self.n += 1
        if self.n == 2:
            self.submit_order(
                self.order_factory.limit(
                    self.inst.id, OrderSide.BUY, self.inst.make_qty(1.0),
                    self.inst.make_price(self.px), time_in_force=TimeInForce.GTC,
                )
            )  # fmt: skip

    def on_order_filled(self, event: Any) -> None:
        self.fills.append(float(event.last_px))


def _limit_fills(price: float) -> list[float]:
    # after bar 1 (open 110, close 112) the market rises: every later low is >= 115
    bars = ladder([100.0, 110.0, 120.0, 130.0, 140.0])
    engine, instruments, _ = build_engine({"TEST/USDT": bars}, "binance", CostModel(), 1e6)
    inst = instruments["TEST/USDT"]
    strat = _LimitBuyer(inst, bar_type_for(inst, "1d"), price)
    engine.add_strategy(strat)
    try:
        engine.run()
    finally:
        engine.dispose()
    return strat.fills


def test_limit_touch_not_filled() -> None:
    assert _limit_fills(115.0) == []  # bar 2's low is exactly 115: touched, never traded through


def test_limit_traded_through_fills_at_limit() -> None:
    assert _limit_fills(116.0) == [116.0]


def test_round_trips_inside_one_bar_are_counted() -> None:
    """Entry at the open, stopped out in the same bar: the position is flat at every close, but
    each round trip is still a trade — with 0 bars held (gate ③'s min_holding_bars)."""
    bars = ladder([100.0] * 6, wick=5.0, up=0.0)  # every bar dips 5 below its open
    res = bt(LongFrom(0, stop_distance=3.0), {"TEST/USDT": bars})
    buys = [f for f in res.fills if f.side == "BUY"]
    assert len(buys) >= 4  # (a stop may fill in parts: Nautilus splits a bar's volume in 4)
    assert res.n_trades == len(buys)
    assert res.avg_holding_bars == 0.0


def test_holding_counts_bars_between_entry_and_exit_fills() -> None:
    bars = ladder(OPENS)
    res = bt(LongFrom(3, 6), {"TEST/USDT": bars}, lookback=len(bars))
    # bought at bar 4's open, sold at bar 7's open: held through the closes of bars 4, 5, 6
    assert res.n_trades == 1 and res.avg_holding_bars == 3


def bars_closing_on(days: list[int], opens: list[float]) -> Bars:
    """Daily bars closing at 00:00 on the given January 2020 days (gaps allowed)."""
    o = np.asarray(opens, dtype=np.float64)
    ts = np.array([np.datetime64(f"2020-01-{d:02d}", "ns") for d in days])
    return Bars("TEST/USDT", "1d", ts, o, o + 5, o - 5, o + 2, np.full(len(o), 1_000.0))


def test_gap_in_data_is_not_a_look_ahead() -> None:
    """Bars close on Jan 2, 5, 6: the bar closing Jan 5 opened Jan 4. A signal at Jan 2's
    close must wait for Jan 4's open — not fill on Jan 2 at a price printed two days later."""
    bars = bars_closing_on([2, 5, 6], [100.0, 130.0, 140.0])
    res = bt(LongFrom(0), {"TEST/USDT": bars})
    fill = res.fills[0]
    jan4 = int(np.datetime64("2020-01-04", "ns").astype(np.int64))
    jan5 = int(bars.ts[1].astype(np.int64))
    assert fill.price == 130.0
    assert jan4 < fill.ts < jan5


def test_overlapping_bars_rejected() -> None:
    bars = ladder(OPENS)
    bad = Bars("TEST/USDT", "1d", bars.ts - np.arange(len(bars)) * np.timedelta64(1, "h"),
               bars.open, bars.high, bars.low, bars.close, bars.volume)  # fmt: skip
    with pytest.raises(ValueError, match="overlap"):
        bt(LongFrom(0), {"TEST/USDT": bad})


def test_base_currency_precision_below_the_size_precision() -> None:
    """Nautilus defines XRP with 6 decimals: quantities at 8 decimals left the XRP balance
    -0.000001 after selling the whole position, and the engine stopped (found on the first
    real-data run). Order sizes must use the base currency's precision."""
    opens = [0.5234 + 0.01 * i for i in range(12)]
    bars = ladder(opens, wick=0.004, up=0.002)
    xrp = Bars("XRP/USDT", "1d", bars.ts, bars.open, bars.high, bars.low, bars.close, bars.volume)
    res = run_backtest(LongFrom(1, stop=6), {"XRP/USDT": xrp}, sizer=FixedNotional(12_345.678))
    assert len(res.equity) == 12  # ran to the last bar
    buys = [f.qty for f in res.fills if f.side == "BUY"]
    sells = [f.qty for f in res.fills if f.side == "SELL"]
    assert buys and sum(buys) == pytest.approx(sum(sells), abs=1e-12)  # flat again
    assert all(round(q, 6) == q for q in buys + sells)  # XRP's own 6 decimals


def test_sortino_uses_downside_deviation() -> None:
    import math

    import numpy as np

    from quantcrucible.execution.engine import BacktestResult

    r = np.array([0.02, -0.01, 0.03, -0.02])
    res = BacktestResult(
        ts=np.array([], dtype="datetime64[ns]"), equity=np.array([1.0, 1.02]), returns=r,
        fills=(), n_trades=0, avg_holding_bars=0.0, turnover=0.0, signals={}, denied_orders=0,
        periods_per_year=365.0,
    )  # fmt: skip
    downside = math.sqrt((0.01**2 + 0.02**2) / 4)
    assert res.sortino == pytest.approx(np.mean(r) / downside * math.sqrt(365.0))
    assert "sortino_is" in res.public_metrics()
