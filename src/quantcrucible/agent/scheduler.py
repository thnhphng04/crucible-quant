"""Trial-budget scheduler (arch §3.1.11, ADR-0024, P2-06).

The budget is split by **statistical trials** — candidates that reached gate ③ — never by
proposals, LLM calls or time: a cheap engine must not consume ``N``. Each (engine, seed) gets a
quota; a worker reserves a slot before evaluating and settles it afterwards, telling whether the
candidate became a trial. An in-flight reservation counts as a trial until settled, so concurrent
workers can never push a quota past its limit (INV-61).

The scheduler keeps its own counts (started from the ledger by the caller) and never touches the
ledger itself, so it is safe across threads.
"""

from __future__ import annotations

import math
import threading
from collections.abc import Mapping

Key = tuple[str, int]  # (engine, seed)


def quotas(trial_budget: int, shares: Mapping[str, float], seeds: int) -> dict[Key, int]:
    """Split ``trial_budget`` by engine share (floor, remainder to the largest shares), then
    evenly across seeds (remainder to the first seeds). Engines with share 0 get no quota."""
    if trial_budget <= 0 or seeds <= 0:
        raise ValueError("trial_budget and seeds must be > 0")
    active = {e: s for e, s in shares.items() if s > 0}
    per_engine = {e: math.floor(trial_budget * s) for e, s in active.items()}
    left = trial_budget - sum(per_engine.values())
    for e in sorted(active, key=lambda e: (-active[e], e))[:left]:
        per_engine[e] += 1
    out: dict[Key, int] = {}
    for e in sorted(per_engine):
        base, extra = divmod(per_engine[e], seeds)
        for seed in range(seeds):
            out[(e, seed)] = base + (1 if seed < extra else 0)
    return out


class TrialScheduler:
    def __init__(self, limits: Mapping[Key, int], measured: Mapping[Key, int] | None = None):
        self._limits = dict(limits)
        self._measured = {k: int((measured or {}).get(k, 0)) for k in self._limits}
        self._in_flight = dict.fromkeys(self._limits, 0)
        self._lock = threading.Lock()

    def keys(self) -> list[Key]:
        return list(self._limits)

    def remaining(self, engine: str, seed: int) -> int:
        k = (engine, seed)
        with self._lock:
            return self._limits[k] - self._measured[k] - self._in_flight[k]

    def reserve(self, engine: str, seed: int) -> bool:
        """Take a slot for one evaluation; False when the quota is (or may be) used up."""
        k = (engine, seed)
        with self._lock:
            if k not in self._limits:
                return False
            if self._measured[k] + self._in_flight[k] >= self._limits[k]:
                return False
            self._in_flight[k] += 1
            return True

    def settle(self, engine: str, seed: int, measured: bool) -> None:
        """Close a reservation: ``measured`` = the candidate became a trial (reached gate ③)."""
        k = (engine, seed)
        with self._lock:
            if self._in_flight[k] <= 0:
                raise RuntimeError(f"settle without a reservation for {k}")
            self._in_flight[k] -= 1
            self._measured[k] += int(measured)

    def measured(self, engine: str, seed: int) -> int:
        with self._lock:
            return self._measured[(engine, seed)]

    def exhausted(self, engine: str, seed: int) -> bool:
        with self._lock:
            k = (engine, seed)
            return self._measured[k] >= self._limits[k]
