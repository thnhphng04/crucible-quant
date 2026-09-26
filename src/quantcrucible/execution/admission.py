"""Entry admission on a shared account (Architecture §3.4, ADR-0031, P3-08).

Ten strategies can want to enter on the same bar. They share one USDT account, so the order in
which they are considered decides who gets in, and that order must not depend on dict iteration,
thread scheduling or which sandbox happened to finish first.

The rule, frozen in the campaign protocol: settle exits, stops, liquidations and funding first,
snapshot equity **once**, then consider entries in canonical order — instrument ascending, long
before short. Every entry in the batch uses the same ``E`` and therefore the same ``R``.

Because ``q`` and ``d`` both freeze at entry, an open position's risk commitment is a constant
number of USDT. So the cap is arithmetic:

    Σ qᵢ·dᵢ + R_new ≤ cap · E

and the "falling equity" breach the requirements describe falls out of it rather than needing a
rule of its own. Nothing here ever widens a stop, adds margin or closes an older position to
make room.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from quantcrucible.core.strategy.base import ScopeDirection

# Longs are considered before shorts on the same instrument. Fixed here rather than derived from
# the string, because the protocol hashes this ordering (ADR-0033).
_SIDE_ORDER = {"long": 0, "short": 1}


@dataclass(frozen=True, slots=True)
class EntryRequest:
    instrument: str
    direction: ScopeDirection
    price: float
    stop_distance: float

    @property
    def sort_key(self) -> tuple[str, int]:
        return (self.instrument, _SIDE_ORDER[self.direction])


@dataclass(frozen=True, slots=True)
class Decision:
    """One request's outcome, admitted or not, with the reason when not.

    A denial is recorded, not swallowed: the requirements ask for denied orders and their reasons
    in the result, so a campaign can tell "no signal" from "no room".
    """

    instrument: str
    direction: ScopeDirection
    admitted: bool
    quantity: float = 0.0
    risk: float = 0.0
    reason: str = ""


def admit_batch(
    requests: list[EntryRequest],
    equity: float,
    risk_pct: float,
    cap: float,
    open_risk: float,
) -> list[Decision]:
    """Decide a whole timestamp's entries against one equity snapshot.

    ``open_risk`` is the summed commitment of positions and pending orders already on the book.
    Returns one decision per request, in canonical order — not in the order they arrived, which
    is the whole point.
    """
    if not (math.isfinite(equity) and equity > 0):
        return [
            Decision(r.instrument, r.direction, False, reason="no_equity")
            for r in sorted(requests, key=lambda r: r.sort_key)
        ]

    risk = equity * risk_pct
    budget = equity * cap
    committed = open_risk
    out: list[Decision] = []
    for request in sorted(requests, key=lambda r: r.sort_key):
        if not (math.isfinite(request.stop_distance) and request.stop_distance > 0):
            out.append(
                Decision(request.instrument, request.direction, False, reason="no_stop_distance")
            )
            continue
        if committed + risk > budget:
            out.append(
                Decision(request.instrument, request.direction, False, reason="portfolio_risk_cap")
            )
            continue
        committed += risk
        out.append(
            Decision(
                instrument=request.instrument,
                direction=request.direction,
                admitted=True,
                quantity=risk / request.stop_distance,
                risk=risk,
            )
        )
    return out
