"""Per-engine MAP-Elites archive, rebuilt from the ledger (arch §3.1.3, §3.1.11, ADR-0024).

The ledger is the only state: an archive is derived from one (campaign, engine, seed)'s trials,
their gate results and the lineage in the audit log, so engines never share an archive in
``isolated`` mode and a restarted run continues where it stopped. Each cell keeps the entry with
the highest score; an entry must be a trial that passed gate ③ and was not rejected at ④
(§3.1.6 #1 — the §3.2 PBO verdict, never a private metric).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any

from quantcrucible.agent.evolution.feature_map import Cell, FeatureMap, cell_id, descriptors
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.gates import G3_IS, G4_PBO


@dataclass(frozen=True, slots=True)
class Entry:
    candidate_id: str
    trial_id: int
    strategy_hash: str
    params: Mapping[str, float | int]
    island: str | None
    categories: tuple[str, ...]
    public: Mapping[str, float]  # gate ③ (+ ④ public, when it ran): IS-only metrics
    cell: Cell
    signature: tuple[str, ...] = ()  # clauses without numbers (grammar.signature)

    @property
    def cell_id(self) -> str:
        return cell_id(self.cell)


Score = Callable[[Entry], float]


def sharpe_score(entry: Entry) -> float:
    return float(entry.public.get("sharpe_is", float("-inf")))


class Archive:
    """Best entry per cell (ties keep the incumbent: the earlier trial)."""

    def __init__(self, score: Score = sharpe_score) -> None:
        self._score = score
        self._cells: dict[Cell, Entry] = {}

    def add(self, entry: Entry) -> bool:
        current = self._cells.get(entry.cell)
        if current is None or self._score(entry) > self._score(current):
            self._cells[entry.cell] = entry
            return True
        return False

    def elites(self) -> list[Entry]:
        return sorted(self._cells.values(), key=lambda e: e.trial_id)

    def __len__(self) -> int:
        return len(self._cells)

    def cells(self) -> set[Cell]:
        return set(self._cells)


def _years(timerange: str) -> float:
    first, last = (date.fromisoformat(s[:10]) for s in timerange.split("/"))
    return max((last - first).days, 1) / 365.25


def load_entries(
    ledger: Ledger, campaign_id: str, engine: str, seed: int, fmap: FeatureMap
) -> list[Entry]:
    """Every archive-eligible trial of one (engine, seed), in trial order."""
    g3 = ledger.latest_gate_results(campaign_id, G3_IS)
    g4 = ledger.latest_gate_results(campaign_id, G4_PBO)
    tags: dict[str, Mapping[str, Any]] = {}
    for _event, _island, detail in ledger.events_for(campaign_id, engine=engine, seed=seed):
        if detail and "descriptors" in detail:
            tags[str(detail["candidate_id"])] = detail["descriptors"]
    out: list[Entry] = []
    for t in ledger.trials(campaign_id, engine=engine, seed=seed):
        passed3, detail3 = g3.get(t.candidate_id, (False, {}))
        if t.verdict != "PASS" or not passed3:
            continue
        if t.candidate_id in g4 and not g4[t.candidate_id][0]:
            continue  # rejected at ④ (PBO ≥ pbo_max)
        public: dict[str, float] = dict(detail3.get("public", {}))
        public.update(g4.get(t.candidate_id, (True, {}))[1].get("public", {}))
        tag = tags.get(t.candidate_id, {})
        cats = tuple(tag.get("categories", ()))
        desc = descriptors(public, _years(t.timerange))
        out.append(
            Entry(
                t.candidate_id, t.id, t.strategy_hash, t.params, t.island, cats, public,
                fmap.cell(desc, cats), tuple(tag.get("signature", ())),
            )
        )  # fmt: skip
    return out


def rebuild(
    ledger: Ledger,
    campaign_id: str,
    engine: str,
    seed: int,
    fmap: FeatureMap,
    score: Score = sharpe_score,
) -> Archive:
    archive = Archive(score)
    for entry in load_entries(ledger, campaign_id, engine, seed, fmap):
        archive.add(entry)
    return archive


def coverage(archives: Iterable[Archive]) -> int:
    """Number of distinct occupied cells across archives."""
    cells: set[Cell] = set()
    for a in archives:
        cells |= a.cells()
    return len(cells)


def trial_cells(
    ledger: Ledger, campaign_id: str, engine: str, seed: int, fmap: FeatureMap
) -> dict[str, Cell]:
    """candidate_id → cell for every measured trial of one (engine, seed), eligible or not —
    where an engine's search went, as opposed to what its archive kept (diagnostics)."""
    g3 = ledger.latest_gate_results(campaign_id, G3_IS)
    tags: dict[str, Mapping[str, Any]] = {}
    for _event, _island, detail in ledger.events_for(campaign_id, engine=engine, seed=seed):
        if detail and "descriptors" in detail:
            tags[str(detail["candidate_id"])] = detail["descriptors"]
    out: dict[str, Cell] = {}
    for t in ledger.trials(campaign_id, engine=engine, seed=seed):
        public = g3.get(t.candidate_id, (False, {}))[1].get("public", {})
        cats = tuple(tags.get(t.candidate_id, {}).get("categories", ()))
        out[t.candidate_id] = fmap.cell(descriptors(public, _years(t.timerange)), cats)
    return out
