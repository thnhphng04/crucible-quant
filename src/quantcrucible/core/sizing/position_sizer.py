"""PositionSizer — ``Signal`` → venue quantity (Architecture §3.4, P3, P4).

Volatility enters the size **exactly once** (the vol-targeting leg); ``stop_distance`` only caps
the loss at the stop, as a ``min`` — never multiplied in. Doubling both the volatility estimate
and the ATR behind the stop therefore halves the size whenever the cap does not bind (the ½-size
test, INV-05).
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
    target_vol: float  # annualized portfolio volatility target (D7)
    max_risk_pct: float  # max loss at the stop, fraction of equity (D12)

    def __post_init__(self) -> None:
        if not 0 < self.target_vol <= 1:
            raise ValueError(f"target_vol must be in (0, 1], got {self.target_vol}")
        if not 0 < self.max_risk_pct < 1:
            raise ValueError(f"max_risk_pct must be in (0, 1), got {self.max_risk_pct}")

    def size(
        self,
        signal: Signal,
        instrument: InstrumentSpec,
        account: Account,
        vol_estimate: float,
        n_active: int,
        idm: float,
        portfolio_scale: float = 1.0,
    ) -> float:
        """Unsigned quantity in venue units (the direction stays on the signal).

        ``vol_estimate`` is the instrument's annualized volatility; ``portfolio_scale`` is the
        uniform factor from the portfolio-level step (§3.4), applied to the vol leg only so the
        stop cap stays a hard bound.
        """
        if n_active < 1:
            raise ValueError("n_active must be >= 1")
        if signal.direction == "flat" or signal.strength == 0.0:
            return 0.0
        if not (math.isfinite(vol_estimate) and vol_estimate > 0):
            return 0.0  # no volatility estimate yet ⇒ no position
        if not (instrument.price > 0 and instrument.unit_value > 0):
            return 0.0
        # 1. vol targeting — the ONLY place volatility enters the size
        inst_target_vol = self.target_vol * idm / n_active
        notional = account.equity * inst_target_vol / vol_estimate * signal.strength
        qty_vol = notional * portfolio_scale / (instrument.price * instrument.unit_value)
        # 2. stop-based risk cap — MIN, not multiply
        qty_cap = (
            account.equity * self.max_risk_pct / (signal.stop_distance * instrument.unit_value)
        )
        # 3. venue units
        return round_to_lot(min(qty_vol, qty_cap), instrument)
