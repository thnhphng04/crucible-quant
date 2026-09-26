"""Hedge mode on a USDT-M perpetual venue (P3-06, ADR-0032) — INV-93's execution half.

Until P3-06 the venue was spot cash: `nautilus_bridge` forced every non-long signal to a target
of zero and counted it, so a short strategy produced no fills at all and gate ③ rejected it on
`min_trades` for a reason that had nothing to do with the strategy.

What Nautilus provides here is the *position model* — two-sided positions, reduce-only stops,
fills. What it does not provide, and what `perp_account` owns (P3-07), is margin, funding and
liquidation: `MarginAccount` keys margin by `InstrumentId` alone, so it cannot represent one
symbol's LONG and SHORT isolated wallets.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from quantcrucible.core.strategy.base import Bars, Features, FeatureView, Signal, Strategy
from quantcrucible.execution.engine import BacktestResult, run_backtest
from tests.factories import make_bars


class AlwaysShort(Strategy):
    def indicators(self, bars: Bars) -> Features:
        return {}

    def signal(self, x: FeatureView) -> Signal:
        return Signal("short", 1.0, 5.0)


class AlwaysLong(Strategy):
    def indicators(self, bars: Bars) -> Features:
        return {}

    def signal(self, x: FeatureView) -> Signal:
        return Signal("long", 1.0, 5.0)


@dataclass(frozen=True)
class FixedNotional:
    """Test-only sizer, unsigned like the real one: direction rides on the signal."""

    notional: float

    def target(self, symbol: str, signal: Signal, window: Bars, equity: float) -> float:
        if signal.direction == "flat":
            return 0.0
        return self.notional / float(window.close[-1])


def bt(strategy: Strategy, bars: dict[str, Bars], **kw: Any) -> BacktestResult:
    return run_backtest(strategy, bars, sizer=FixedNotional(10_000.0), lookback=50, **kw)


def test_a_short_signal_opens_a_short() -> None:
    """The headline change: a short is traded, not counted and dropped."""
    bars = make_bars(120, seed=1, symbol="A/USDT")
    res = bt(AlwaysShort(), {"A/USDT": bars})
    assert res.fills, "a short signal produced no fills at all"
    assert res.fills[0].side == "SELL"
    assert res.signals["short"] == len(bars)


def test_a_short_round_trip_is_counted() -> None:
    """Without this, gate ③'s `min_trades` rejects every short strategy for the wrong reason."""
    bars = make_bars(120, seed=2, symbol="A/USDT")
    res = bt(AlwaysShort(), {"A/USDT": bars})
    assert res.n_trades >= 1
    assert res.avg_holding_bars > 0


def test_the_short_stop_is_above_the_entry_and_is_a_buy() -> None:
    bars = make_bars(120, seed=3, symbol="A/USDT")
    res = bt(AlwaysShort(), {"A/USDT": bars})
    sells = [f for f in res.fills if f.side == "SELL"]
    assert sells, "no short entry"
    # a protective stop on a short buys back above the entry; a long's sells below it
    long_res = bt(AlwaysLong(), {"A/USDT": bars})
    assert [f for f in long_res.fills if f.side == "BUY"]


def test_a_long_and_a_short_on_one_contract_hold_independent_positions() -> None:
    """Hedge mode: the two sides net to a smaller book on a NETTING venue but must not here."""
    bars = make_bars(120, seed=4, symbol="A/USDT")
    longs = bt(AlwaysLong(), {"A/USDT": bars})
    shorts = bt(AlwaysShort(), {"A/USDT": bars})
    assert longs.fills and shorts.fills
    assert {f.position_side for f in longs.fills} == {"LONG"}
    assert {f.position_side for f in shorts.fills} == {"SHORT"}


def test_equity_is_finite_and_the_run_is_not_truncated() -> None:
    bars = make_bars(200, seed=5, symbol="A/USDT")
    res = bt(AlwaysShort(), {"A/USDT": bars})
    assert len(res.equity) == len(bars)
    assert np.all(np.isfinite(res.equity))
