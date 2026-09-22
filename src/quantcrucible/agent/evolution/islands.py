"""Islands and migration for engine C-gp (arch §3.1.5, ADR-0024, P2-12).

``N = C + 1`` islands: one seeded from each strategy category of the grammar, plus one open
island. They evolve independently; every ``interval`` generations (a generation = one evaluated
candidate per island) the top ``fraction`` of each island's population, by ranking score, is
copied to its neighbour on a ring (i → i + 1). A migration is an audit event, so an island's
population — the entries that evolved on it plus the entries that migrated to it, each once —
is rebuilt from the ledger like everything else. A child bred from a migrant belongs to the
island it was bred on, i.e. the destination.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from quantcrucible.agent.evolution.archive import Archive, Entry
from quantcrucible.agent.grammar import CATEGORIES
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent

MIGRATION_INTERVAL = 10  # generations (arch §3.1.8)
MIGRATION_FRACTION = 0.10  # top 10% (arch §3.1.5)


def island_names() -> tuple[str, ...]:
    """``i0``…: one island per grammar category, then the open island (``N = C + 1``)."""
    return tuple(f"i{k}" for k in range(len(CATEGORIES) + 1))


def island_category(island: str) -> str | None:
    """The category an island is seeded with; ``None`` for the open island."""
    k = int(island[1:])
    return CATEGORIES[k] if k < len(CATEGORIES) else None


@dataclass(frozen=True, slots=True)
class Migration:
    source: str
    destination: str
    candidate_ids: tuple[str, ...]


def populations(
    entries: Iterable[Entry], migrations: Iterable[Migration], islands: Sequence[str]
) -> dict[str, list[Entry]]:
    """island → its population: native entries, then migrants (each candidate once)."""
    by_id = {e.candidate_id: e for e in entries}
    pops: dict[str, dict[str, Entry]] = {i: {} for i in islands}
    for e in by_id.values():
        if e.island in pops:
            pops[e.island][e.candidate_id] = e
    for m in migrations:
        if m.destination not in pops:
            continue
        for cid in m.candidate_ids:
            if cid in by_id:
                pops[m.destination].setdefault(cid, by_id[cid])
    return {i: sorted(p.values(), key=lambda e: e.trial_id) for i, p in pops.items()}


def island_archive(population: Iterable[Entry], score: Mapping[str, float]) -> Archive:
    archive = Archive(lambda e: score.get(e.candidate_id, float("-inf")))
    for e in population:
        archive.add(e)
    return archive


def plan_migration(
    pops: Mapping[str, Sequence[Entry]],
    score: Mapping[str, float],
    fraction: float = MIGRATION_FRACTION,
) -> list[Migration]:
    """Top ``fraction`` of each island (at least one when it has any) to the next island on
    the ring — skipping candidates the destination already holds (no duplicated migrant)."""
    order = list(pops)
    out: list[Migration] = []
    for k, src in enumerate(order):
        pop = pops[src]
        if not pop:
            continue
        dst = order[(k + 1) % len(order)]
        if dst == src:
            continue
        n = max(1, math.floor(len(pop) * fraction))
        best = sorted(pop, key=lambda e: (-score.get(e.candidate_id, float("-inf")), e.trial_id))
        held = {e.candidate_id for e in pops[dst]}
        moved = tuple(e.candidate_id for e in best[:n] if e.candidate_id not in held)
        if moved:
            out.append(Migration(src, dst, moved))
    return out


# ── the ledger is the only state (ADR-0024) ────────────────────────────────────────────────
def record_migration(
    ledger: Ledger, campaign_id: str, engine: str, seed: int, run_id: str, m: Migration
) -> None:
    ledger.log_event(
        GenerationEvent(
            run_id=run_id, campaign_id=campaign_id, engine=engine, seed=seed, agent="engine",
            model_used="none", event=Event.MIGRATION, island=m.destination,
            detail={"from": m.source, "to": m.destination, "candidates": list(m.candidate_ids)},
        )
    )  # fmt: skip


def migrations(ledger: Ledger, campaign_id: str, engine: str, seed: int) -> list[Migration]:
    out: list[Migration] = []
    for event, _island, detail in ledger.events_for(campaign_id, engine=engine, seed=seed):
        if event == Event.MIGRATION and detail:
            out.append(
                Migration(str(detail["from"]), str(detail["to"]), tuple(detail["candidates"]))
            )
    return out
