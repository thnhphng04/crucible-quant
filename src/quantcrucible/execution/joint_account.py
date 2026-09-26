"""One account replay across many slots (Architecture §3.2.1, §3.4, ADR-0032, P3-08).

A portfolio used to be N independently backtested, independently self-financed return streams
combined by weight (:func:`quantcrucible.validation.portfolio.combine`). That stops describing
the real thing the moment ten strategies share one USDT balance, one margin pool and one kill
switch: their fills interact through the account, not through a weighted sum of their returns.

So the replay takes every slot's fills — each produced by its own single-strategy backtest —
plus the mark and funding series, and walks them through one :class:`PerpAccount` in time order.

**Per-bar order is fixed and hashed into the campaign protocol**: funding first, then
liquidation on the mark, then the bar's fills. Funding before liquidation matters, because
funding leaves the isolated wallet and so can be what tips a position over.

A slot's *contribution* is the account PnL attributable to that slot — realized PnL, fees and
funding. Starting equity plus the summed contributions equals account equity, which is what
makes a per-instrument chart an honest decomposition rather than an invented subaccount.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt

from quantcrucible.core.strategy.base import ScopeDirection
from quantcrucible.execution.margin import BracketTable
from quantcrucible.execution.perp_account import Liquidated, PerpAccount

# Both sides, typed once so every loop over them narrows instead of needing an ignore.
_SIDES: tuple[ScopeDirection, ...] = ("long", "short")

Slot = tuple[str, str]  # (instrument, direction)


@dataclass(frozen=True, slots=True)
class SlotFill:
    """One fill, tagged with the slot that produced it.

    ``opening`` says whether this fill opens the slot's position or closes it. It is carried
    rather than inferred, because under hedge mode a BUY opens a long and closes a short.
    """

    ts: int  # ns
    instrument: str
    direction: ScopeDirection
    qty: float
    price: float
    commission: float
    opening: bool

    @property
    def slot(self) -> Slot:
        return (self.instrument, self.direction)

    @property
    def sort_key(self) -> tuple[int, str, int]:
        # Closes before opens at one timestamp: a slot must free its margin before another
        # asks for it. Then canonical slot order, as in `admission`.
        return (self.ts, self.instrument, 0 if self.direction == "long" else 1)


@dataclass(slots=True)
class ReplayResult:
    ts: npt.NDArray[np.datetime64]
    equity: npt.NDArray[np.float64]
    contributions: dict[Slot, float] = field(default_factory=dict)
    funding_paid: dict[Slot, float] = field(default_factory=dict)
    liquidations: list[Slot] = field(default_factory=list)
    denied: list[tuple[Slot, str]] = field(default_factory=list)

    @property
    def returns(self) -> npt.NDArray[np.float64]:
        if len(self.equity) < 2:
            return np.zeros(0)
        return self.equity[1:] / self.equity[:-1] - 1.0


def replay_account(
    fills: Sequence[SlotFill],
    ts: npt.NDArray[np.datetime64],
    marks: Mapping[str, npt.NDArray[np.float64]],
    funding: Mapping[str, npt.NDArray[np.float64]],
    tables: Mapping[str, BracketTable],
    initial_cash: float,
    leverage: int,
) -> ReplayResult:
    """Walk one account through every slot's fills.

    ``marks`` and ``funding`` are per instrument, indexed like ``ts``. A funding rate of 0 means
    no payment; a **missing** instrument means no funding data, which callers upstream must have
    already refused (INV-94) rather than silently treating as zero.
    """
    account = PerpAccount(balance=initial_cash, tables=tables)
    result = ReplayResult(ts=ts, equity=np.empty(len(ts), dtype=np.float64))
    contributions: dict[Slot, float] = {}
    funding_paid: dict[Slot, float] = {}

    by_bar: dict[int, list[SlotFill]] = {}
    ts_ns = ts.astype("datetime64[ns]").astype(np.int64)
    for fill in sorted(fills, key=lambda f: f.sort_key):
        k = int(np.searchsorted(ts_ns, fill.ts, side="left"))
        by_bar.setdefault(min(k, len(ts) - 1), []).append(fill)

    def credit(slot: Slot, amount: float) -> None:
        contributions[slot] = contributions.get(slot, 0.0) + amount

    for i in range(len(ts)):
        bar_marks = {s: float(series[i]) for s, series in marks.items()}

        # 1. funding, before liquidation: paying it can be what tips a wallet over
        for instrument, rates in funding.items():
            rate = float(rates[i])
            if rate == 0.0:
                continue
            mark = bar_marks.get(instrument)
            if mark is None:
                continue
            for side in _SIDES:
                wallet = account.wallet(instrument, side)
                if wallet is None:
                    continue
                payment = wallet.qty * mark * rate
                cost = payment if side == "long" else -payment
                slot = (instrument, side)
                funding_paid[slot] = funding_paid.get(slot, 0.0) + cost
                credit(slot, -cost)
            account.apply_funding(instrument, rate, mark)

        # 2. liquidation on the mark, not on the trade price
        for instrument, mark in bar_marks.items():
            for side in _SIDES:
                if account.wallet(instrument, side) is None:
                    continue
                if account.is_liquidated(instrument, side, mark):
                    lost = account.liquidate(instrument, side)
                    slot = (instrument, side)
                    credit(slot, lost)
                    result.liquidations.append(slot)

        # 3. the bar's fills
        for fill in by_bar.get(i, []):
            credit(fill.slot, -fill.commission)
            account.balance -= fill.commission
            if fill.opening:
                if account.wallet(fill.instrument, fill.direction) is not None:
                    continue  # already in: the engine re-issues, the account does not stack
                try:
                    account.open(fill.instrument, fill.direction, fill.qty, fill.price, leverage)
                except (Liquidated, ValueError) as exc:
                    result.denied.append((fill.slot, type(exc).__name__))
            else:
                if account.wallet(fill.instrument, fill.direction) is None:
                    continue  # already liquidated: nothing left to close
                credit(fill.slot, account.close(fill.instrument, fill.direction, fill.price))

        result.equity[i] = account.equity(bar_marks)

    result.contributions = contributions
    result.funding_paid = funding_paid
    return result
