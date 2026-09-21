"""The Risk layer inside the execution path (Architecture §3.4, P4, P5, ADR-0010).

:class:`RiskSizer` turns each bar's :class:`Signal` into a target quantity with
:class:`~quantcrucible.core.sizing.position_sizer.PositionSizer`. It keeps the portfolio-level
state the sizer needs — IDM and the uniform portfolio scale, both re-estimated at every
rebalance boundary (``rebalance``: weekly / monthly / quarterly) from the trailing returns of the
universe — and enforces the gross-leverage cap on every bar. The same object serves backtest and
live (P5): it sees only closed bars and the account's equity.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd

from quantcrucible.core.sizing.position_sizer import Account, InstrumentSpec, PositionSizer
from quantcrucible.core.sizing.vol_target import (
    IDM_CAP,
    VOL_SPAN,
    ewma_vol,
    instrument_diversification_multiplier,
    portfolio_scale,
)
from quantcrucible.core.strategy.base import Bars, Signal

CORR_WINDOW = 250  # bars of trailing returns for the IDM / covariance estimate


@dataclass(frozen=True, slots=True)
class RiskSettings:
    """Locked per campaign: ``target_vol`` / ``max_risk_pct`` / ``rebalance`` from Group B, the
    rest from the lock's ``derived.sizing`` (ADR-0010)."""

    target_vol: float = 0.10
    max_risk_pct: float = 0.01
    rebalance: str = "monthly"
    vol_span: int = VOL_SPAN
    max_leverage: float = 1.0
    idm_cap: float = IDM_CAP


def _period_key(ts: np.datetime64, rebalance: str) -> tuple[int, int]:
    t = pd.Timestamp(ts)
    if rebalance == "weekly":
        iso = t.isocalendar()
        return (int(iso[0]), int(iso[1]))
    if rebalance == "monthly":
        return (t.year, t.month)
    if rebalance == "quarterly":
        return (t.year, (t.month - 1) // 3)
    raise ValueError(f"unknown rebalance schedule {rebalance!r}")


class RiskSizer:
    """Target quantity per symbol and bar; implements the bridge's ``TargetSizer`` protocol."""

    def __init__(
        self,
        symbols: Sequence[str],
        settings: RiskSettings,
        periods_per_year: float,
        lot_step: float = 0.0,
    ) -> None:
        self.settings = settings
        self._sizer = PositionSizer(settings.target_vol, settings.max_risk_pct)
        self._symbols = list(symbols)
        self._ppy = periods_per_year
        self._lot_step = lot_step
        self._closes: dict[str, pd.Series] = {}
        self._notional: dict[str, float] = dict.fromkeys(self._symbols, 0.0)
        self._period: tuple[int, int] | None = None
        self.idm = 1.0
        self.scale = 1.0

    def target(self, symbol: str, signal: Signal, window: Bars, equity: float) -> float:
        price = float(window.close[-1])
        self._closes[symbol] = pd.Series(
            window.close, index=pd.DatetimeIndex(window.ts.astype("datetime64[ns]"))
        )
        key = _period_key(window.ts[-1], self.settings.rebalance)
        if key != self._period:
            self._period = key
            self._rebalance(equity)
        if signal.direction != "long" or equity <= 0:  # spot, cash account: long or flat
            self._notional[symbol] = 0.0
            return 0.0
        vol = ewma_vol(window.close, self.settings.vol_span, self._ppy)
        spec = InstrumentSpec(price=price, lot_step=self._lot_step)
        qty = self._sizer.size(
            signal, spec, Account(equity), vol if vol is not None else float("nan"),
            n_active=len(self._symbols), idm=self.idm, portfolio_scale=self.scale,
        )  # fmt: skip
        # gross-leverage cap on every bar, given what the other symbols already hold
        others = sum(abs(v) for s, v in self._notional.items() if s != symbol)
        room = max(self.settings.max_leverage * equity - others, 0.0)
        qty = min(qty, room / price)
        self._notional[symbol] = qty * price
        return qty

    def _returns(self) -> pd.DataFrame:
        frame = pd.concat(self._closes, axis=1).sort_index().iloc[-(CORR_WINDOW + 1) :]
        return frame.pct_change(fill_method=None).iloc[1:]

    def _rebalance(self, equity: float) -> None:
        """IDM and portfolio scale from trailing returns (§3.4 portfolio step)."""
        if not self._closes:
            return
        rets = self._returns()
        if len(rets.dropna(how="all")) < 30:
            return
        corr = rets.corr(min_periods=30).reindex(index=self._symbols, columns=self._symbols)
        self.idm = instrument_diversification_multiplier(
            corr.to_numpy(dtype=np.float64), cap=self.settings.idm_cap
        )
        cov = (rets.cov(min_periods=30) * self._ppy).reindex(
            index=self._symbols, columns=self._symbols
        )
        notionals = np.array([self._notional[s] for s in self._symbols])
        # the scale applies to the vol leg of every size, so estimate on the unscaled book
        base = notionals / self.scale if self.scale > 0 else notionals
        self.scale = portfolio_scale(
            base, np.nan_to_num(cov.to_numpy(dtype=np.float64)), equity,
            self.settings.target_vol, self.settings.max_leverage,
        )  # fmt: skip
