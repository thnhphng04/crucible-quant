"""The Risk layer inside the execution path (Architecture §3.4, P4′, P5, ADR-0010, ADR-0031).

:class:`RiskSizer` turns each bar's :class:`Signal` into a target quantity with
:class:`~quantcrucible.core.sizing.position_sizer.PositionSizer`. Since ADR-0031 retired P4 it
holds no portfolio state: size is ``R/d``, which needs only the account's equity, the signal's
stop distance and the venue's lot step. IDM, the portfolio scale and the trailing-return window
went with vol targeting.

The gross-leverage cap went too. It was a cash-account rule (`max_leverage = 1.0`), and what
replaces it is not a per-bar clamp but two later checks: the bracket bound on leverage (P3-05)
and the aggregate stop-risk cap at admission (P3-08). Until those land, nothing here bounds
total exposure — which is safe only because no campaign opens inside phase 3.

The same object serves backtest and live (P5): it sees only closed bars and the account's equity.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from quantcrucible.core.sizing.position_sizer import Account, InstrumentSpec, PositionSizer
from quantcrucible.core.strategy.base import Bars, Signal


@dataclass(frozen=True, slots=True)
class RiskSettings:
    """Locked per campaign: ``max_risk_pct`` from Group B (D12). Perpetual leverage and margin
    settings join it in P3-05."""

    max_risk_pct: float = 0.01


class RiskSizer:
    """Target quantity per symbol and bar; implements the bridge's ``TargetSizer`` protocol."""

    def __init__(
        self,
        symbols: Sequence[str],
        settings: RiskSettings,
        lot_step: float | Mapping[str, float] = 0.0,
    ) -> None:
        self.settings = settings
        self._sizer = PositionSizer(settings.max_risk_pct)
        self._symbols = list(symbols)
        self._lot_step = (
            dict(lot_step) if isinstance(lot_step, Mapping) else dict.fromkeys(symbols, lot_step)
        )  # per symbol: each base currency has its own precision

    def target(self, symbol: str, signal: Signal, window: Bars, equity: float) -> float:
        # Unsigned, both sides (P3-06): the venue is a USDT-M perpetual and the direction rides
        # on the signal, which `nautilus_bridge` applies. Refusing a short here left the bridge's
        # short branch unreachable on the path every gate runs.
        if signal.direction == "flat":
            return 0.0
        spec = InstrumentSpec(
            price=float(window.close[-1]), lot_step=self._lot_step.get(symbol, 0.0)
        )
        return self._sizer.size(signal, spec, Account(equity))
