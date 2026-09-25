"""Trial-budget scheduler (arch §3.1.11, ADR-0024, ADR-0033, P2-06, P3-12).

The budget is split by **statistical trials** — candidates that reached gate ③ — never by
proposals, LLM calls or time: a cheap engine must not consume ``N``. Each unit of search gets a
quota; a worker reserves a slot before evaluating and settles it afterwards, telling whether the
candidate became a trial. An in-flight reservation counts as a trial until settled, so concurrent
workers can never push a quota past its limit (INV-61).

Since P3-12 the unit is ``(instrument, direction, engine, seed)``, not ``(engine, seed)``: each
``(instrument, direction)`` is searched independently, with its own archive and its own RNG
stream. The budget must divide exactly across every unit, because a comparison whose arms did
not get the same quota is not a comparison (INV-73) — an uneven split is refused rather than
rounded away.

The scheduler keeps its own counts (started from the ledger by the caller) and never touches the
ledger itself, so it is safe across threads.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from quantcrucible.core.strategy.base import ScopeDirection
from quantcrucible.ledger.records import LEGACY_INSTRUMENT


@dataclass(frozen=True, slots=True, order=True)
class Scope:
    """One ``(instrument, direction)`` searched on its own."""

    instrument: str
    direction: ScopeDirection

    def __str__(self) -> str:
        return f"{self.instrument}-{self.direction}"


@dataclass(frozen=True, slots=True, order=True)
class Key:
    """One unit of search. Ordered so reports and fill order are deterministic."""

    instrument: str
    direction: ScopeDirection
    engine: str
    seed: int

    @property
    def scope(self) -> Scope:
        return Scope(self.instrument, self.direction)

    def __str__(self) -> str:
        return f"{self.instrument}-{self.direction}-{self.engine}-s{self.seed}"


def quotas(
    trial_budget: int,
    shares: Mapping[str, float],
    seeds: int,
    scopes: Sequence[Scope],
) -> dict[Key, int]:
    """Split ``trial_budget`` evenly across every (scope, engine, seed).

    Engines with share 0 get no quota and do not count as units. The remaining engines share the
    budget equally: an engine share other than an equal split is refused, because the comparison
    protocol requires the same quota per unit and a 0.8/0.2 split cannot give one.
    """
    if trial_budget <= 0 or seeds <= 0:
        raise ValueError("trial_budget and seeds must be > 0")
    if not scopes:
        raise ValueError("at least one scope is required")
    active = sorted(e for e, s in shares.items() if s > 0)
    if not active:
        raise ValueError("no engine has a positive share")
    weights = {shares[e] for e in active}
    if len(weights) != 1:
        raise ValueError(
            f"engine shares must be equal for a comparison, got { {e: shares[e] for e in active} }"
        )
    units = len(scopes) * len(active) * seeds
    per_unit, remainder = divmod(trial_budget, units)
    if remainder or per_unit == 0:
        raise ValueError(
            f"trial_budget {trial_budget} does not divide exactly across {units} units "
            f"({len(scopes)} scopes x {len(active)} engines x {seeds} seeds): "
            f"use a multiple of {units}"
        )
    return {
        Key(scope.instrument, scope.direction, engine, seed): per_unit
        for scope in sorted(scopes)
        for engine in active
        for seed in range(seeds)
    }


class TrialScheduler:
    def __init__(self, limits: Mapping[Key, int], measured: Mapping[Key, int] | None = None):
        self._limits = dict(limits)
        self._measured = {k: int((measured or {}).get(k, 0)) for k in self._limits}
        self._in_flight = dict.fromkeys(self._limits, 0)
        self._lock = threading.Lock()

    def quota_keys(self) -> list[Key]:
        return list(self._limits)

    def remaining(self, key: Key) -> int:
        with self._lock:
            return self._limits[key] - self._measured[key] - self._in_flight[key]

    def reserve(self, key: Key) -> bool:
        """Take a slot for one evaluation; False when the quota is (or may be) used up."""
        with self._lock:
            if key not in self._limits:
                return False
            if self._measured[key] + self._in_flight[key] >= self._limits[key]:
                return False
            self._in_flight[key] += 1
            return True

    def settle(self, key: Key, measured: bool) -> None:
        """Close a reservation: ``measured`` = the candidate became a trial (reached gate ③)."""
        with self._lock:
            if self._in_flight[key] <= 0:
                raise RuntimeError(f"settle without a reservation for {key}")
            self._in_flight[key] -= 1
            self._measured[key] += int(measured)

    def measured(self, key: Key) -> int:
        with self._lock:
            return self._measured[key]

    def exhausted(self, key: Key) -> bool:
        with self._lock:
            return self._measured[key] >= self._limits[key]


def campaign_scopes(lock: Mapping[str, Any]) -> list[Scope]:
    """Every (instrument, direction) this campaign searches.

    A pre-P3 lock names spot symbols and no directions; it has exactly one scope, the legacy
    whole-basket long-or-flat search, so those campaigns keep the shape they ran under.
    """
    data = lock["research"].get("data", {})
    instruments = list(data.get("instruments") or [])
    if not instruments:
        return [Scope(LEGACY_INSTRUMENT, "long")]
    directions = [str(d) for d in data.get("directions", ("long", "short"))]
    return [
        Scope(str(i), d)  # type: ignore[arg-type]
        for i in sorted(instruments)
        for d in directions
    ]
