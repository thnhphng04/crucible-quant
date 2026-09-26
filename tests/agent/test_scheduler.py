"""Trial-budget scheduler (arch §3.1.11, P2-06, P3-12) — INV-61.

The quota *split* moved to `test_scope_quotas.py` when the unit widened to
`(instrument, direction, engine, seed)`. What stays here is the reservation invariant, which did
not change: many workers reserving, measuring or failing at random never push a unit past its
quota, and the quota is used up when attempts suffice.
"""

import random
import threading
from collections import Counter

import pytest

from quantcrucible.agent.scheduler import Key, TrialScheduler
from tests.factories import unit


def test_existing_trials_count_against_the_quota() -> None:
    k = unit("gp", 0)
    s = TrialScheduler({k: 5}, measured={k: 4})
    assert s.remaining(k) == 1
    assert s.reserve(k)
    assert not s.reserve(k)  # the in-flight one might become the 5th trial
    s.settle(k, measured=False)  # it failed before gate ③: not a trial
    assert s.reserve(k)
    s.settle(k, measured=True)
    assert s.exhausted(k) and s.measured(k) == 5


def test_an_unknown_unit_has_no_quota() -> None:
    s = TrialScheduler({unit("gp", 0): 5})
    assert not s.reserve(unit("random", 0))
    with pytest.raises(KeyError):
        s.settle(unit("random", 0), measured=True)


def test_a_unit_of_another_scope_has_no_quota() -> None:
    """Widened from the engine/seed form: a scope cannot spend its neighbour's budget."""
    s = TrialScheduler({unit("gp", 0, instrument="BTCUSDT"): 5})
    assert not s.reserve(unit("gp", 0, instrument="ETHUSDT"))
    assert not s.reserve(unit("gp", 0, instrument="BTCUSDT", direction="short"))


def test_quota_never_exceeded_concurrently() -> None:
    """INV-61: concurrent reservations never push a unit past its quota."""
    limits = {
        unit("gp", 0, instrument="BTCUSDT"): 40,
        unit("gp", 1, instrument="BTCUSDT"): 25,
        unit("random", 0, instrument="ETHUSDT", direction="short"): 30,
    }
    s = TrialScheduler(limits)
    measured: Counter[Key] = Counter()
    guard = threading.Lock()

    def worker(i: int) -> None:
        rng = random.Random(i)
        for _ in range(400):
            key = rng.choice(list(limits))
            if not s.reserve(key):
                continue
            ok = rng.random() < 0.6
            with guard:
                measured[key] += ok
            s.settle(key, measured=ok)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert dict(measured) == limits
    assert all(s.exhausted(k) for k in limits)
