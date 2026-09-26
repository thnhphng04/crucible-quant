"""PositionSizer — ``Signal`` → venue quantity (Architecture §3.4, P3, P4′).

``Q = R / d`` with ``R = max_risk_pct × equity`` and ``d = stop_distance``: every position loses
exactly ``R`` if price reaches its stop, whatever the instrument's volatility. Volatility still
reaches the size exactly once, through ``d`` (typically k × ATR), never through a vol-targeting
leg — ADR-0031 retired P4 and with it ``target_vol``, IDM and the portfolio scale.

Lot rounding floors, so realized risk lands in ``[R − lot_step·d, R]`` and never above it
(INV-90). A caller that needs the shortfall bounded must check it: the sizer reports a quantity,
not a refusal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from quantcrucible.core.strategy.base import Signal


@dataclass(frozen=True, slots=True)
class InstrumentSpec:
    """What sizing needs to know about one instrument at one moment.

    ``multiplier`` is the contract size (1 for spot/shares), ``fx_rate`` converts one unit of the
    quote currency into the account's base currency, ``lot_step`` / ``min_qty`` / ``max_qty`` are
    the venue's quantity rules (0 / 0 / None = unconstrained).
    """

    price: float
    multiplier: float = 1.0
    fx_rate: float = 1.0
    lot_step: float = 0.0
    min_qty: float = 0.0
    max_qty: float | None = None

    @property
    def unit_value(self) -> float:
        """Base-currency value of one unit of quantity per unit of price."""
        return self.multiplier * self.fx_rate


@dataclass(frozen=True, slots=True)
class Account:
    equity: float  # base currency


def round_to_lot(qty: float, instrument: InstrumentSpec) -> float:
    """Round DOWN to the lot step (never round risk up); below ``min_qty`` ⇒ 0; clip at max."""
    if not math.isfinite(qty) or qty <= 0:
        return 0.0
    if instrument.max_qty is not None:
        qty = min(qty, instrument.max_qty)
    if instrument.lot_step > 0:
        qty = math.floor(qty / instrument.lot_step + 1e-9) * instrument.lot_step
    return qty if qty >= instrument.min_qty and qty > 0 else 0.0


@dataclass(frozen=True, slots=True)
class PositionSizer:
    max_risk_pct: float  # the loss at the stop, as a fraction of equity (D12)

    def __post_init__(self) -> None:
        if not 0 < self.max_risk_pct < 1:
            raise ValueError(f"max_risk_pct must be in (0, 1), got {self.max_risk_pct}")

    def size(self, signal: Signal, instrument: InstrumentSpec, account: Account) -> float:
        """Unsigned quantity in venue units (the direction stays on the signal).

        ``Q = R / (d · unit_value)`` with ``R = max_risk_pct × equity``. ``signal.strength`` does
        not scale ``R``: the portfolio cap is defined on ``R``, so a strength-scaled budget would
        make that cap non-binding in a way nothing tracks (ADR-0031).
        """
        if signal.direction == "flat":
            return 0.0
        if not (account.equity > 0 and instrument.price > 0 and instrument.unit_value > 0):
            return 0.0
        risk = account.equity * self.max_risk_pct
        denominator = signal.stop_distance * instrument.unit_value
        if not (math.isfinite(denominator) and denominator > 0):
            return 0.0
        return round_to_lot(risk / denominator, instrument)
