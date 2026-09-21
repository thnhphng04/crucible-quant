"""The Risk layer in the execution path (§3.4, P4, ADR-0010): RiskSizer + run_backtest."""

from __future__ import annotations

import numpy as np
import pytest

from quantcrucible.core.sizing.position_sizer import Account, InstrumentSpec, PositionSizer
from quantcrucible.core.sizing.vol_target import ewma_vol
from quantcrucible.core.strategy.base import FLAT, Bars, Signal
from quantcrucible.core.zoo.ema_crossover import GeneratedStrategy
from quantcrucible.execution.engine import run_backtest
from quantcrucible.execution.risk import RiskSettings, RiskSizer
from tests.factories import make_bars

LONG = Signal("long", 1.0, 1.0)  # a tight stop: the cap (1% / 1.0 per unit) never binds here


def window(bars: Bars, end: int) -> Bars:
    return Bars(bars.symbol, bars.timeframe, bars.ts[:end], bars.open[:end], bars.high[:end],
                bars.low[:end], bars.close[:end], bars.volume[:end])  # fmt: skip


def test_target_is_the_position_sizer_on_the_ewma_vol() -> None:
    bars = make_bars(60, seed=1, vol=0.03)
    sizer = RiskSizer(["A"], RiskSettings(), periods_per_year=365)
    qty = sizer.target("A", LONG, bars, equity=100_000)
    vol = ewma_vol(bars.close, 25, 365)
    assert vol is not None
    expected = PositionSizer(0.10, 0.01).size(
        LONG, InstrumentSpec(price=float(bars.close[-1])), Account(100_000), vol, 1, 1.0
    )
    assert qty == pytest.approx(expected)
    assert sizer.target("A", FLAT, bars, 100_000) == 0.0
    assert sizer.target("A", Signal("short", 1.0, 1.0), bars, 100_000) == 0.0  # spot


def test_half_size_through_the_risk_sizer() -> None:
    """INV-05 end to end: returns twice as volatile (and the ATR stop with them) ⇒ ½ size."""
    bars = make_bars(80, seed=2, vol=0.02)
    rets = np.diff(bars.close) / bars.close[:-1]
    close2 = bars.close[0] * np.concatenate(([1.0], np.cumprod(1 + 2 * rets)))
    wild = Bars("A", "1d", bars.ts, close2, close2, close2, close2, bars.volume)
    calm = Bars("A", "1d", bars.ts, bars.close, bars.close, bars.close, bars.close, bars.volume)
    price_ratio = close2[-1] / bars.close[-1]
    a = RiskSizer(["A"], RiskSettings(), 365).target("A", Signal("long", 1, 1.0), calm, 1e5)
    b = RiskSizer(["A"], RiskSettings(), 365).target("A", Signal("long", 1, 2.0), wild, 1e5)
    assert b * price_ratio == pytest.approx(a / 2, rel=1e-9)  # same notional logic, half


def test_gross_leverage_cap_across_symbols() -> None:
    bars = make_bars(60, seed=3, vol=0.001)  # very calm ⇒ vol targeting wants a huge position
    settings = RiskSettings(max_leverage=1.0, max_risk_pct=0.5)
    sizer = RiskSizer(["A", "B"], settings, 365)
    qa = sizer.target("A", LONG, bars, 100_000)
    qb = sizer.target("B", LONG, bars, 100_000)
    price = float(bars.close[-1])
    assert (qa + qb) * price <= 100_000 * 1.0 + 1e-6
    assert qb * price == pytest.approx(100_000 - qa * price)


def test_idm_reestimated_at_each_rebalance_boundary() -> None:
    a = make_bars(120, seed=4, symbol="A")
    b = make_bars(120, seed=5, symbol="B")  # independent ⇒ IDM ≈ √2
    sizer = RiskSizer(["A", "B"], RiskSettings(rebalance="monthly"), 365)
    assert sizer.idm == 1.0
    for end in range(40, 121):
        sizer.target("A", FLAT, window(a, end), 1e5)
        sizer.target("B", FLAT, window(b, end), 1e5)
    assert sizer.idm == pytest.approx(np.sqrt(2), rel=0.15)


def test_backtest_with_risk_sizing_trades_within_the_leverage_cap() -> None:
    bars = {"A/USDT": make_bars(400, seed=6, symbol="A/USDT", drift=0.001),
            "B/USDT": make_bars(400, seed=7, symbol="B/USDT", drift=0.001)}  # fmt: skip
    strat = GeneratedStrategy({"fast": 5, "slow": 40, "k_atr": 2.0})
    res = run_backtest(strat, bars, risk=RiskSettings(max_leverage=0.5))
    assert res.n_trades > 0 and res.denied_orders == 0
    exposure = np.zeros(len(res.ts))
    ts_ns = res.ts.astype("datetime64[ns]").astype(np.int64)
    for f in res.fills:
        k = int(np.searchsorted(ts_ns, f.ts, side="left"))
        exposure[k:] += (f.qty if f.side == "BUY" else -f.qty) * f.price
    # notional at entry prices stays inside the cap (+ one band of drift between resizes)
    assert float(np.max(exposure / res.equity)) <= 0.5 * 1.3


def test_unknown_rebalance_rejected() -> None:
    sizer = RiskSizer(["A"], RiskSettings(rebalance="daily"), 365)
    with pytest.raises(ValueError, match="rebalance"):
        sizer.target("A", LONG, make_bars(30), 1e5)
