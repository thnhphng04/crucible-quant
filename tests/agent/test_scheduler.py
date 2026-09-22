"""Trial-budget scheduler (arch §3.1.11, P2-06) — INV-61."""

import random
import threading
from collections import Counter

import pytest

from quantcrucible.agent.scheduler import TrialScheduler, quotas


def test_quotas_split_the_budget_by_engine_share_then_seed() -> None:
    q = quotas(1000, {"gp": 0.5, "random": 0.5, "quantevolve": 0.0, "simple_loop": 0.0}, seeds=3)
    assert q == {
        ("gp", 0): 167, ("gp", 1): 167, ("gp", 2): 166,
        ("random", 0): 167, ("random", 1): 167, ("random", 2): 166,
    }  # fmt: skip
    assert sum(q.values()) == 1000


def test_uneven_shares() -> None:
    q = quotas(100, {"gp": 0.8, "random": 0.2}, seeds=1)
    assert q == {("gp", 0): 80, ("random", 0): 20}


def test_existing_trials_count_against_the_quota() -> None:
    s = TrialScheduler({("gp", 0): 5}, measured={("gp", 0): 4})
    assert s.remaining("gp", 0) == 1
    assert s.reserve("gp", 0)
    assert not s.reserve("gp", 0)  # the in-flight one might become the 5th trial
    s.settle("gp", 0, measured=False)  # it failed before gate ③: not a trial
    assert s.reserve("gp", 0)
    s.settle("gp", 0, measured=True)
    assert s.exhausted("gp", 0) and s.measured("gp", 0) == 5


def test_unknown_engine_seed_has_no_quota() -> None:
    s = TrialScheduler({("gp", 0): 5})
    assert not s.reserve("random", 0)
    with pytest.raises(KeyError):
        s.settle("random", 0, measured=True)


def test_quota_never_exceeded_concurrently() -> None:
    """INV-61: many workers reserving, measuring or failing at random never push a (engine, seed)
    past its quota — and the quota is used up when attempts suffice."""
    limits = {("gp", 0): 40, ("gp", 1): 25, ("random", 0): 30}
    s = TrialScheduler(limits)
    measured: Counter[tuple[str, int]] = Counter()
    guard = threading.Lock()

    def worker(i: int) -> None:
        rng = random.Random(i)
        for _ in range(400):
            key = rng.choice(list(limits))
            if not s.reserve(*key):
                continue
            ok = rng.random() < 0.6
            with guard:
                measured[key] += ok
            s.settle(*key, measured=ok)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert dict(measured) == limits
    assert all(s.exhausted(*k) for k in limits)
