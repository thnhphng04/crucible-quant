"""Binance USDT-M bracket margin (Architecture §3.5, ADR-0032, P3-05).

The venue sizes margin in *tiers*: the larger a position's notional, the lower the leverage it
may use and the higher its maintenance-margin rate. Each tier carries a continuity term
(``amount``) that keeps maintenance margin from jumping where one tier ends and the next begins.

The table itself is **data, not code**. Binance serves it from a signed endpoint
(``GET /fapi/v1/leverageBracket``) and publishes no history at all, so a campaign fetches a
snapshot, records it in its lock, and states in the ADR that applying today's brackets to a 2021
backtest is an **assumption** — never a reconstruction. What lives here is the arithmetic:

    initial margin      = notional / leverage
    maintenance margin  = notional × mmr − amount
    isolated liquidation (long)  = (entry·q − wallet − amount) / (q · (1 − mmr))
    isolated liquidation (short) = (entry·q + wallet + amount) / (q · (1 + mmr))

Nothing here knows about positions or accounts; :mod:`quantcrucible.execution.perp_account`
(P3-07) owns the isolated wallets that call into it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from quantcrucible.core.strategy.base import ScopeDirection


@dataclass(frozen=True, slots=True)
class Bracket:
    """One notional tier. ``cap`` is inclusive: a notional exactly at the cap stays in this
    tier, which is how the venue draws the boundary."""

    cap: float
    max_leverage: int
    mmr: float  # maintenance margin rate
    amount: float  # maintenance amount, the continuity term

    def __post_init__(self) -> None:
        if not (math.isfinite(self.cap) and self.cap > 0):
            raise ValueError(f"cap must be finite and > 0, got {self.cap}")
        if self.max_leverage < 1:
            raise ValueError(f"max_leverage must be >= 1, got {self.max_leverage}")
        if not 0 < self.mmr < 1:
            raise ValueError(f"mmr must be in (0, 1), got {self.mmr}")
        if self.amount < 0:
            raise ValueError(f"amount must be >= 0, got {self.amount}")


@dataclass(frozen=True, slots=True)
class BracketTable:
    """One symbol's tiers, ascending by notional cap."""

    symbol: str
    brackets: tuple[Bracket, ...]

    def __post_init__(self) -> None:
        if not self.brackets:
            raise ValueError(f"{self.symbol}: a bracket table needs at least one bracket")
        caps = [b.cap for b in self.brackets]
        if caps != sorted(caps) or len(set(caps)) != len(caps):
            raise ValueError(f"{self.symbol}: brackets must be in ascending order of cap")

    def bracket_for(self, notional: float) -> Bracket:
        for bracket in self.brackets:
            if notional <= bracket.cap:
                return bracket
        raise ValueError(
            f"{self.symbol}: notional {notional:,.2f} is above the largest bracket "
            f"({self.brackets[-1].cap:,.2f})"
        )

    def initial_margin(self, notional: float, leverage: int) -> float:
        bracket = self.bracket_for(notional)
        if leverage > bracket.max_leverage:
            raise ValueError(
                f"{self.symbol}: leverage {leverage} requested but the bracket allows at most "
                f"{bracket.max_leverage} at notional {notional:,.2f}"
            )
        if leverage < 1:
            raise ValueError(f"leverage must be >= 1, got {leverage}")
        return notional / leverage

    def maintenance_margin(self, notional: float) -> float:
        bracket = self.bracket_for(notional)
        return notional * bracket.mmr - bracket.amount

    def lowest_leverage(self, notional: float, free: float) -> int | None:
        """The lowest integer leverage the free balance can fund, or ``None`` if even the
        bracket's maximum cannot be funded.

        Lowest is safest: it puts the most margin behind the position and so pushes liquidation
        furthest away. The bracket's ceiling is the only thing stopping it going lower.
        """
        bracket = self.bracket_for(notional)
        for leverage in range(1, bracket.max_leverage + 1):
            if notional / leverage <= free:
                return leverage
        return None

    def liquidation_price(
        self, direction: ScopeDirection, quantity: float, entry: float, wallet: float
    ) -> float:
        """Isolated-margin liquidation for one side, from that side's own wallet.

        ``wallet`` is the isolated balance backing this position and nothing else: under hedge
        mode a symbol's LONG and SHORT wallets are separate, so one side's liquidation never
        reads the other side's margin.
        """
        if quantity <= 0:
            raise ValueError(f"quantity must be > 0, got {quantity}")
        bracket = self.bracket_for(abs(quantity) * entry)
        notional = entry * quantity
        if direction == "long":
            return (notional - wallet - bracket.amount) / (quantity * (1 - bracket.mmr))
        return (notional + wallet + bracket.amount) / (quantity * (1 + bracket.mmr))
