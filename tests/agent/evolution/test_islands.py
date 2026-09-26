"""Islands, migration and parent sampling of engine C-gp (arch §3.1.4–3.1.5, P2-12) — INV-67.

MadEvolve's public code has an island model that is never called (arch §3.1.10 X3); these tests
prove ours is: parents come from the island being processed, migrants are never duplicated, and
a migrant's children stay on the destination island.
"""

from pathlib import Path

import numpy as np
import pytest

from quantcrucible.agent.evolution.archive import Entry
from quantcrucible.agent.evolution.islands import (
    Migration,
    island_archive,
    island_category,
    island_names,
    migrations,
    plan_migration,
    populations,
    record_migration,
)
from quantcrucible.agent.evolution.sampling import sample_mate, sample_parent
from quantcrucible.agent.grammar import CATEGORIES
from quantcrucible.ledger.db import Ledger
from tests.factories import unit

ISLANDS = island_names()


def _e(cid: str, tid: int, island: str, cell: int) -> Entry:
    return Entry(cid, tid, f"h{cid}", {"n1": 5}, island, ("trend",), {"sharpe_is": 1.0}, (cell,))


def test_there_is_one_island_per_category_plus_an_open_one() -> None:
    assert len(ISLANDS) == len(CATEGORIES) + 1
    assert [island_category(i) for i in ISLANDS] == [*CATEGORIES, None]


def test_island_integration() -> None:
    """INV-67: (a) the parent is drawn from the island being processed; (b) migrants are not
    duplicated at the destination; (c) a migrant's children belong to the destination."""
    entries = [
        _e(f"{i}-{k}", 10 * n + k, i, k) for n, i in enumerate(ISLANDS[:3]) for k in range(5)
    ]
    score = {e.candidate_id: float(e.trial_id % 7) for e in entries}
    first = plan_migration(populations(entries, [], ISLANDS[:3]), score)
    assert {(m.source, m.destination) for m in first} == {
        ("i0", "i1"), ("i1", "i2"), ("i2", "i0"),
    }  # fmt: skip
    pops = populations(entries, first, ISLANDS[:3])
    rng = np.random.default_rng(0)
    for pop in pops.values():
        elites = island_archive(pop, score).elites()
        members = {e.candidate_id for e in pop}
        for _ in range(200):  # (a)
            parent = sample_parent(rng, elites, pop)
            assert parent is not None and parent.candidate_id in members
    # (b) the same plan again moves nothing already held; each candidate appears once per island
    second = plan_migration(pops, score)
    held = {i: {e.candidate_id for e in p} for i, p in pops.items()}
    for m in second:
        assert not set(m.candidate_ids) & held[m.destination]
    for pop in populations(entries, first + second, ISLANDS[:3]).values():
        ids = [e.candidate_id for e in pop]
        assert len(ids) == len(set(ids))
    # (c) a migrant bred on i1 yields a child whose island is i1 — and it lives only there
    migrant = next(e for e in pops["i1"] if e.island == "i0")
    child = _e("child", 99, "i1", 7)
    after = populations([*entries, child], first, ISLANDS[:3])
    assert migrant.candidate_id in {e.candidate_id for e in after["i1"]}
    assert "child" in {e.candidate_id for e in after["i1"]}
    assert "child" not in {e.candidate_id for e in after["i0"] + after["i2"]}


def test_an_empty_island_yields_no_parent() -> None:
    rng = np.random.default_rng(1)
    pops = populations([_e("a", 1, "i0", 0)], [], ISLANDS)
    assert sample_parent(rng, [], pops["i3"]) is None
    assert plan_migration({"i0": pops["i0"], "i1": []}, {"a": 1.0})[0].destination == "i1"
    assert sample_mate(rng, pops["i0"][0], [], pops["i0"]) is None  # no one else to mate with


def test_alpha_splits_elites_and_population() -> None:
    rng = np.random.default_rng(2)
    elite = _e("elite", 1, "i0", 0)
    pop = [elite, *(_e(f"p{k}", k + 2, "i0", 0) for k in range(9))]
    draws = [sample_parent(rng, [elite], pop, alpha=0.5) for _ in range(4000)]
    share = sum(d is elite for d in draws) / len(draws)
    assert share == pytest.approx(0.5 + 0.5 / 10, abs=0.03)


def test_migrations_round_trip_through_the_ledger(tmp_path: Path) -> None:
    lg = Ledger.open(tmp_path / "l.db")
    lg.open_campaign("c1", "2027-01-01/2028-01-01", lock_hash="h")
    m = Migration("i0", "i1", ("a", "b"))
    record_migration(lg, "c1", unit("gp", 0), "r", m)
    record_migration(lg, "c1", unit("gp", 1), "r", Migration("i2", "i3", ("z",)))
    assert migrations(lg, "c1", unit("gp", 0)) == [m]
