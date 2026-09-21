"""Risk & Sizing (Architecture §3.4, P4) — INV-05: volatility enters size exactly once; the stop
is a ``min`` cap, never a multiplier."""

from __future__ import annotations

import math

import numpy as np
import pytest

from quantcrucible.core.sizing.position_sizer import (
    Account,
    InstrumentSpec,
    PositionSizer,
    round_to_lot,
)
from quantcrucible.core.sizing.vol_target import (
    ewma_vol,
    instrument_diversification_multiplier,
    portfolio_scale,
)
from quantcrucible.core.strategy.base import FLAT, Signal

SIZER = PositionSizer(target_vol=0.10, max_risk_pct=0.01)
ACCOUNT = Account(equity=100_000.0)
BTC = InstrumentSpec(price=50_000.0)


def test_half_size_when_vol_doubles() -> None:
    """The mandatory phase-1 test (§3.4): double both vol_estimate and ATR ⇒ exactly ½."""
    atr = 1_000.0
    base = SIZER.size(Signal("long", 1.0, 2 * atr), BTC, ACCOUNT, 0.60, n_active=5, idm=1.5)
    doubled = SIZER.size(Signal("long", 1.0, 2 * (2 * atr)), BTC, ACCOUNT, 1.20, 5, 1.5)
    # the stop cap does not bind here: 0.01 · 1e5 / 2000 = 0.5 BTC > vol size 0.1 BTC
    assert base == pytest.approx(100_000 * 0.10 * 1.5 / 5 / 0.60 / 50_000)
    assert doubled == pytest.approx(base / 2, rel=1e-12)


def test_stop_cap_is_min_not_multiplier() -> None:
    wide, tight = 30_000.0, 60_000.0  # stop distances in price units
    vol_qty = 100_000 * 0.10 * 1.0 / 1 / 0.60 / 50_000
    loose = SIZER.size(Signal("long", 1.0, 100.0), BTC, ACCOUNT, 0.60, 1, 1.0)
    assert loose == pytest.approx(vol_qty)  # cap far away: pure vol targeting
    capped = SIZER.size(Signal("long", 1.0, wide), BTC, ACCOUNT, 0.60, 1, 1.0)
    assert capped == pytest.approx(100_000 * 0.01 / wide)  # binds as the min
    assert capped < vol_qty
    # when the cap binds, vol no longer matters — it is not multiplied in
    assert SIZER.size(Signal("long", 1.0, tight), BTC, ACCOUNT, 0.30, 1, 1.0) == pytest.approx(
        100_000 * 0.01 / tight
    )


def test_strength_scales_the_vol_leg_and_flat_is_zero() -> None:
    full = SIZER.size(Signal("long", 1.0, 100.0), BTC, ACCOUNT, 0.60, 1, 1.0)
    half = SIZER.size(Signal("long", 0.5, 100.0), BTC, ACCOUNT, 0.60, 1, 1.0)
    assert half == pytest.approx(full / 2)
    assert SIZER.size(FLAT, BTC, ACCOUNT, 0.60, 1, 1.0) == 0.0
    assert SIZER.size(Signal("long", 1.0, 100.0), BTC, ACCOUNT, float("nan"), 1, 1.0) == 0.0


def test_fx_and_multiplier_convert_to_base_currency() -> None:
    # a contract worth 50 × price in a quote currency worth 0.5 base-currency units
    fut = InstrumentSpec(price=4_000.0, multiplier=50.0, fx_rate=0.5)
    qty = SIZER.size(Signal("long", 1.0, 10.0), fut, ACCOUNT, 0.20, 1, 1.0)
    assert qty == pytest.approx(100_000 * 0.10 / 0.20 / (4_000 * 50 * 0.5))
    capped = SIZER.size(Signal("long", 1.0, 400.0), fut, ACCOUNT, 0.20, 1, 1.0)
    assert capped == pytest.approx(100_000 * 0.01 / (400 * 50 * 0.5))


def test_portfolio_scale_multiplies_only_the_vol_leg() -> None:
    sig = Signal("long", 1.0, 100.0)
    one = SIZER.size(sig, BTC, ACCOUNT, 0.60, 1, 1.0)
    assert SIZER.size(sig, BTC, ACCOUNT, 0.60, 1, 1.0, portfolio_scale=2.0) == pytest.approx(
        2 * one
    )
    wide = Signal("long", 1.0, 30_000.0)  # cap binds: scaling cannot lift it
    assert SIZER.size(wide, BTC, ACCOUNT, 0.60, 1, 1.0, portfolio_scale=3.0) == pytest.approx(
        100_000 * 0.01 / 30_000
    )


def test_round_to_lot_respects_step_min_max() -> None:
    inst = InstrumentSpec(price=1.0, lot_step=0.01, min_qty=0.05, max_qty=2.0)
    assert round_to_lot(0.1299, inst) == pytest.approx(0.12)  # floor, never round up risk
    assert round_to_lot(0.049, inst) == 0.0  # below the minimum: no trade
    assert round_to_lot(7.5, inst) == 2.0
    assert round_to_lot(-1.0, inst) == 0.0
    assert round_to_lot(0.3, InstrumentSpec(price=1.0)) == 0.3  # no step: unchanged
    lots = InstrumentSpec(price=1.0, lot_step=100.0)  # e.g. shares in round lots
    assert round_to_lot(250.0, lots) == 200.0


def test_invalid_sizer_settings_rejected() -> None:
    with pytest.raises(ValueError):
        PositionSizer(target_vol=0.0, max_risk_pct=0.01)
    with pytest.raises(ValueError):
        PositionSizer(target_vol=0.1, max_risk_pct=1.5)
    with pytest.raises(ValueError):
        SIZER.size(Signal("long", 1.0, 100.0), BTC, ACCOUNT, 0.6, n_active=0, idm=1.0)


# ── vol estimate, IDM, portfolio scaling ─────────────────────────────────────────────


def test_ewma_vol_recovers_a_known_volatility() -> None:
    rng = np.random.default_rng(0)
    daily = 0.03
    close = 100 * np.cumprod(1 + rng.normal(0, daily, 4000))
    est = ewma_vol(close, span=500, periods_per_year=365)
    assert est == pytest.approx(daily * math.sqrt(365), rel=0.1)
    assert ewma_vol(close[:5], span=25, periods_per_year=365) is None  # too short
    doubled = 100 * np.cumprod(1 + 2 * np.diff(close) / close[:-1])
    assert ewma_vol(doubled, 25, 365) == pytest.approx(2 * ewma_vol(close, 25, 365), rel=1e-6)  # type: ignore[operator]


def test_idm_is_one_for_one_instrument_and_grows_with_diversification() -> None:
    assert instrument_diversification_multiplier(np.ones((1, 1))) == pytest.approx(1.0)
    assert instrument_diversification_multiplier(np.ones((4, 4))) == pytest.approx(1.0)
    assert instrument_diversification_multiplier(np.eye(4)) == pytest.approx(2.0)
    half = np.full((2, 2), 0.5) + np.eye(2) * 0.5
    assert instrument_diversification_multiplier(half) == pytest.approx(1 / math.sqrt(0.75))
    assert instrument_diversification_multiplier(np.eye(100)) == pytest.approx(2.5)  # capped
    neg = np.array([[1.0, -0.9], [-0.9, 1.0]])  # negative ρ floored at 0 (no free lunch)
    assert instrument_diversification_multiplier(neg) == pytest.approx(math.sqrt(2))


def test_portfolio_scale_back_to_target_with_leverage_cap() -> None:
    cov = np.array([[0.04, 0.0], [0.0, 0.04]])  # two instruments at 20% vol, uncorrelated
    notionals = np.array([10_000.0, 10_000.0])
    # portfolio vol = √(2 · 1e8 · 0.04) / 1e5 = 2.83% ⇒ scale to 10%
    s = portfolio_scale(notionals, cov, 100_000, target_vol=0.10, max_leverage=10.0)
    assert s == pytest.approx(0.10 / (math.sqrt(2 * 1e8 * 0.04) / 1e5))
    capped = portfolio_scale(notionals, cov, 100_000, target_vol=0.10, max_leverage=0.3)
    assert capped == pytest.approx(0.3 * 100_000 / 20_000)  # gross 20k ⇒ at most 30k
    assert portfolio_scale(np.zeros(2), cov, 100_000, 0.10, 1.0) == 1.0  # nothing held
