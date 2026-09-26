"""The search unit widens to (instrument, direction, engine, seed) (P3-12, ADR-0033) — INV-96.

Each scope is an independent arm: its own quota, its own RNG stream, its own archive. Two things
fail silently if this is done carelessly, and both have a test here.

The first is the RNG. `engine_seed` folded only engine and seed, so twenty arms of one
scope-pair would draw *identical* sequences and the "independent search per scope" premise would
be false at the RNG level — while producing perfectly plausible results.

The second is the split. A budget that does not divide exactly across scopes x arms x seeds
leaves some scope short, and a comparison whose arms did not get the same quota is not a
comparison. It is refused rather than rounded.
"""

from __future__ import annotations

import pytest

from quantcrucible.agent.run import engine_seed
from quantcrucible.agent.scheduler import Scope, TrialScheduler, quotas

SHARES = {"gp": 0.5, "random": 0.5}
SCOPES = (Scope("BTCUSDT", "long"), Scope("BTCUSDT", "short"), Scope("ETHUSDT", "long"))


def test_the_budget_divides_across_scopes_arms_and_seeds() -> None:
    q = quotas(trial_budget=540, shares=SHARES, seeds=3, scopes=SCOPES)
    assert len(q) == 3 * 2 * 3 == 18  # scopes x arms x seeds
    assert set(q.values()) == {30}
    assert sum(q.values()) == 540


def test_the_requirements_600_trial_shape_divides() -> None:
    """5 instruments x 2 directions x 2 engines x 3 seeds x 10 = 600, from the frozen spec."""
    scopes = tuple(Scope(i, d) for i in ("A", "B", "C", "D", "E") for d in ("long", "short"))
    q = quotas(trial_budget=600, shares=SHARES, seeds=3, scopes=scopes)
    assert len(q) == 60 and set(q.values()) == {10}


def test_a_budget_that_does_not_divide_exactly_is_refused() -> None:
    """A comparison whose arms did not get the same quota is not a comparison (INV-73)."""
    with pytest.raises(ValueError, match="does not divide"):
        quotas(trial_budget=600, shares=SHARES, seeds=3, scopes=SCOPES)  # 600 / 18 is not whole


def test_unequal_engine_shares_are_refused() -> None:
    """0.8/0.2 cannot give both arms the same quota, so it is not a comparison budget."""
    with pytest.raises(ValueError, match="must be equal"):
        quotas(540, {"gp": 0.8, "random": 0.2}, seeds=3, scopes=SCOPES)


def test_the_key_carries_the_whole_scope() -> None:
    q = quotas(trial_budget=108, shares=SHARES, seeds=3, scopes=SCOPES)
    key = next(iter(q))
    assert key.instrument in {"BTCUSDT", "ETHUSDT"}
    assert key.direction in {"long", "short"}
    assert key.engine in SHARES
    assert 0 <= key.seed < 3


def test_two_scopes_draw_different_sequences() -> None:
    """Silent and plausible if wrong: every scope-pair would search the same thing."""
    seeds = {engine_seed("gp", 0, instrument=s.instrument, direction=s.direction) for s in SCOPES}
    assert len(seeds) == len(SCOPES)


def test_the_two_sides_of_one_contract_draw_different_sequences() -> None:
    a = engine_seed("gp", 0, instrument="BTCUSDT", direction="long")
    b = engine_seed("gp", 0, instrument="BTCUSDT", direction="short")
    assert a != b


def test_engines_and_seeds_still_separate_within_a_scope() -> None:
    kw = {"instrument": "BTCUSDT", "direction": "long"}
    assert engine_seed("gp", 0, **kw) != engine_seed("random", 0, **kw)
    assert engine_seed("gp", 0, **kw) != engine_seed("gp", 1, **kw)


def test_the_legacy_call_still_works_and_is_stable() -> None:
    """Pre-P3 campaigns recorded runs under the two-argument form; it must not move."""
    assert engine_seed("gp", 0) == 1_000_003 * 2
    assert engine_seed("random", 1) == 1_000_003 + 1


def test_quota_is_counted_within_its_own_scope() -> None:
    q = quotas(trial_budget=36, shares=SHARES, seeds=3, scopes=SCOPES)  # 2 per unit
    scheduler = TrialScheduler(q)
    key = next(iter(q))
    other = next(k for k in q if k != key)
    for _ in range(q[key]):
        assert scheduler.reserve(key)
        scheduler.settle(key, measured=True)
    assert not scheduler.reserve(key)  # this scope is exhausted
    assert scheduler.reserve(other)  # its neighbour is untouched


def test_in_flight_reservations_still_bound_the_quota() -> None:
    """INV-61, widened: concurrent workers cannot push a scope past its limit."""
    q = quotas(trial_budget=36, shares=SHARES, seeds=3, scopes=SCOPES)  # 2 per unit
    scheduler = TrialScheduler(q)
    key = next(iter(q))
    assert all(scheduler.reserve(key) for _ in range(q[key]))
    assert not scheduler.reserve(key)  # nothing settled yet, but the quota is spoken for


def test_the_legacy_sentinel_never_travels_as_an_instrument() -> None:
    """`legacy_spot` labels absent scope; it is not a contract.

    Letting it through made a candidate ask for bars that do not exist, which the phase-2
    end-to-end runs caught and the unit tests did not — so it is pinned here.
    """
    legacy = Scope("legacy_spot", "long")
    key = next(iter(quotas(6, SHARES, seeds=3, scopes=(legacy,))))
    assert key.is_legacy
    assert key.searched_instrument is None

    real = next(iter(quotas(6, SHARES, seeds=3, scopes=(Scope("BTCUSDT", "short"),))))
    assert not real.is_legacy
    assert real.searched_instrument == "BTCUSDT"
