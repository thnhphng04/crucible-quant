"""Risk & Sizing (Architecture §3.4, P4′) — INV-90: position size is risk over stop distance.

``Q = R/d`` with ``R = max_risk_pct × equity``. Volatility reaches the size only through ``d``;
there is no vol-targeting leg. ADR-0031 retired P4 and INV-05, so the tests that proved the old
``min(qty_vol, qty_cap)`` form are gone and these prove its negation instead: the volatility
estimate does not enter at all (``tests/execution/test_risk.py``), and the loss at the stop is
the budget rather than a ceiling the vol leg usually sat below.

Reference values are computed by hand from `R = max_risk_pct × equity` and `Q = R / (d · uv)`,
never read back from the implementation.
"""

from __future__ import annotations

import pytest

from quantcrucible.core.sizing.position_sizer import (
    Account,
    InstrumentSpec,
    PositionSizer,
    round_to_lot,
)
from quantcrucible.core.strategy.base import FLAT, Signal

SIZER = PositionSizer(max_risk_pct=0.01)
ACCOUNT = Account(equity=100_000.0)  # ⇒ R = 1_000
BTC = InstrumentSpec(price=50_000.0)


def test_risk_at_the_stop_is_exactly_max_risk_pct() -> None:
    """INV-90: the loss at the stop IS the budget, not a cap the size usually sits under."""
    qty = SIZER.size(Signal("long", 1.0, 25.0), BTC, ACCOUNT)
    assert qty == pytest.approx(40.0)  # 1_000 / 25
    assert qty * 25.0 == pytest.approx(1_000.0)  # = 0.01 × equity, exactly


def test_doubling_the_stop_halves_the_size() -> None:
    """``d`` enters exactly once, linearly — the only way volatility reaches the size."""
    base = SIZER.size(Signal("long", 1.0, 25.0), BTC, ACCOUNT)
    doubled = SIZER.size(Signal("long", 1.0, 50.0), BTC, ACCOUNT)
    assert doubled == pytest.approx(base / 2, rel=1e-12)
    assert doubled * 50.0 == pytest.approx(base * 25.0)  # same risk either way


def test_lot_rounding_only_ever_lowers_risk() -> None:
    """Rounding is one-sided: realized risk lands in ``[R − lot_step·d, R]``, never above."""
    inst = InstrumentSpec(price=50_000.0, lot_step=0.01)
    d = 30.0
    qty = SIZER.size(Signal("long", 1.0, d), inst, ACCOUNT)
    assert qty == pytest.approx(33.33)  # floor(1_000/30 / 0.01) × 0.01
    realized = qty * d
    assert realized <= 1_000.0
    assert realized > 1_000.0 - 0.01 * d


def test_a_flat_signal_is_zero() -> None:
    assert SIZER.size(FLAT, BTC, ACCOUNT) == 0.0


def test_a_short_is_sized_like_a_long() -> None:
    """``size`` is unsigned: direction lives on the signal, not in the quantity."""
    long = SIZER.size(Signal("long", 1.0, 25.0), BTC, ACCOUNT)
    short = SIZER.size(Signal("short", 1.0, 25.0), BTC, ACCOUNT)
    assert short == pytest.approx(long)


def test_fx_and_multiplier_convert_to_base_currency() -> None:
    # a contract worth 50 × price in a quote currency worth 0.5 base-currency units
    fut = InstrumentSpec(price=4_000.0, multiplier=50.0, fx_rate=0.5)  # unit_value 25
    qty = SIZER.size(Signal("long", 1.0, 400.0), fut, ACCOUNT)
    assert qty == pytest.approx(1_000 / (400 * 25))  # 0.1 contracts


def test_no_position_without_a_usable_price_or_equity() -> None:
    assert SIZER.size(Signal("long", 1.0, 25.0), BTC, Account(0.0)) == 0.0
    assert SIZER.size(Signal("long", 1.0, 25.0), InstrumentSpec(price=0.0), ACCOUNT) == 0.0


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
        PositionSizer(max_risk_pct=0.0)
    with pytest.raises(ValueError):
        PositionSizer(max_risk_pct=1.5)
