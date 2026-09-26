"""The Risk layer in the execution path (§3.4, P4′, ADR-0010, ADR-0031): RiskSizer + run_backtest.

INV-90's negation lives here: the volatility of the window no longer reaches the size at all.
Under the retired P4 the same two windows produced a 2:1 size ratio
(``test_half_size_through_the_risk_sizer``); under ``Q = R/d`` they produce the same size.
"""

from __future__ import annotations

import numpy as np
import pytest

from quantcrucible.core.sizing.position_sizer import Account, InstrumentSpec, PositionSizer
from quantcrucible.core.strategy.base import FLAT, Bars, Signal
from quantcrucible.core.zoo.ema_crossover import GeneratedStrategy
from quantcrucible.execution.engine import run_backtest
from quantcrucible.execution.risk import RiskSettings, RiskSizer
from tests.factories import make_bars

LONG = Signal("long", 1.0, 1.0)


def window(bars: Bars, end: int) -> Bars:
    return Bars(bars.symbol, bars.timeframe, bars.ts[:end], bars.open[:end], bars.high[:end],
                bars.low[:end], bars.close[:end], bars.volume[:end])  # fmt: skip


def test_target_is_the_position_sizer_on_the_stop_distance() -> None:
    bars = make_bars(60, seed=1, vol=0.03)
    sizer = RiskSizer(["A"], RiskSettings())
    qty = sizer.target("A", LONG, bars, equity=100_000)
    expected = PositionSizer(0.01).size(
        LONG, InstrumentSpec(price=float(bars.close[-1])), Account(100_000)
    )
    assert qty == pytest.approx(expected)
    assert sizer.target("A", FLAT, bars, 100_000) == 0.0


def test_a_short_is_still_flat_on_a_spot_cash_venue() -> None:
    """Current behaviour, not a desired one: the spot venue cannot hold a short. P3-06 flips
    this, and `PositionSizer` already sizes a short — the refusal is the venue's, not the
    sizer's."""
    bars = make_bars(60, seed=1, vol=0.03)
    sizer = RiskSizer(["A"], RiskSettings())
    assert sizer.target("A", Signal("short", 1.0, 1.0), bars, 100_000) == 0.0


def test_vol_estimate_does_not_change_the_size() -> None:
    """INV-90, stated as the negation of the retired INV-05.

    Two windows over the same bars, one with returns twice as volatile. With the same
    ``stop_distance`` the sizes are identical: the volatility estimate is gone from the path.
    """
    bars = make_bars(80, seed=2, vol=0.02)
    rets = np.diff(bars.close) / bars.close[:-1]
    close2 = bars.close[0] * np.concatenate(([1.0], np.cumprod(1 + 2 * rets)))
    wild = Bars("A", "1d", bars.ts, close2, close2, close2, close2, bars.volume)
    calm = Bars("A", "1d", bars.ts, bars.close, bars.close, bars.close, bars.close, bars.volume)
    stop = Signal("long", 1.0, 2.0)
    a = RiskSizer(["A"], RiskSettings()).target("A", stop, calm, 1e5)
    b = RiskSizer(["A"], RiskSettings()).target("A", stop, wild, 1e5)
    assert b == pytest.approx(a, rel=1e-12)


def test_the_stop_distance_is_what_moves_the_size() -> None:
    bars = make_bars(60, seed=3, vol=0.02)
    sizer = RiskSizer(["A"], RiskSettings())
    tight = sizer.target("A", Signal("long", 1.0, 1.0), bars, 1e5)
    wide = sizer.target("A", Signal("long", 1.0, 2.0), bars, 1e5)
    assert wide == pytest.approx(tight / 2, rel=1e-12)


def test_risk_at_the_stop_holds_through_the_risk_sizer() -> None:
    bars = make_bars(60, seed=4, vol=0.02)
    qty = RiskSizer(["A"], RiskSettings(max_risk_pct=0.01)).target("A", LONG, bars, 1e5)
    assert qty * 1.0 == pytest.approx(0.01 * 1e5)


def test_backtest_with_risk_sizing_runs() -> None:
    bars = {"A/USDT": make_bars(400, seed=6, symbol="A/USDT", drift=0.001),
            "B/USDT": make_bars(400, seed=7, symbol="B/USDT", drift=0.001)}  # fmt: skip
    strat = GeneratedStrategy({"fast": 5, "slow": 40, "k_atr": 2.0})
    res = run_backtest(strat, bars, risk=RiskSettings(max_risk_pct=0.001))
    assert res.n_trades > 0 and res.denied_orders == 0
