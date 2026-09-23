"""Engine C-gp (arch §3.1.11 v0.6, §3.1.3–3.1.5, ADR-0024/0026, P2-13): typed GP over the
grammar, on islands, with a MAP-Elites archive per island.

Each ``next()`` works on the next island of the round (a generation = one proposal per island):

1. rebuild the state from the ledger — this (engine, seed)'s eligible entries, their genomes
   (recorded with each submission), the migrations, the ranking scores;
2. every ``MIGRATION_INTERVAL`` generations, record a ring migration of each island's top 10%;
3. draw a parent from the island (Eq. 1) — none yet ⇒ a fresh genome from the island's
   category (the island's seeding, or its re-seeding when nothing survived);
4. choose an operator (parameter-only children capped at ``param_only_max``, D20), draw a mate
   for crossover, breed; a child already proposed is never proposed again.

The child belongs to the island it was bred on. The engine reads the ledger's ``public`` metrics
and gate verdicts only (INV-68). It never evaluates: the pipeline does, through the gates.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import numpy as np

from quantcrucible.agent.engines.random_search import Proposal
from quantcrucible.agent.evolution.archive import Entry, load_entries
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.evolution.islands import (
    MIGRATION_INTERVAL,
    island_archive,
    island_category,
    island_names,
    migrations,
    plan_migration,
    populations,
    record_migration,
)
from quantcrucible.agent.evolution.operators import (
    MutationKind,
    OperatorFailed,
    breed,
    changed_params_only,
    choose_kind,
)
from quantcrucible.agent.evolution.ranking import RankContext, scores
from quantcrucible.agent.evolution.sampling import sample_mate, sample_parent
from quantcrucible.agent.grammar import (
    CATEGORY_CLAUSES,
    CLAUSE_TYPES,
    Genome,
    GrammarConfig,
    load_genome,
    render_genome,
    sample_genome,
)
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event

ENGINE = "gp"
MAX_TRIES = 20


def _clause_types(category: str | None) -> tuple[type, ...]:
    """Clause types an island samples when seeding: its category's, or all for the open one."""
    return CLAUSE_TYPES if category is None else CATEGORY_CLAUSES[category]


class GpSearch:
    engine = ENGINE

    def __init__(
        self,
        ledger: Ledger,
        campaign_id: str,
        seed: int,
        rng_seed: int,
        fmap: FeatureMap,
        periods_per_year: float,
        param_only_max: float,
        run_id: str,
        config: GrammarConfig | None = None,
    ) -> None:
        self.ledger = ledger
        self.campaign_id = campaign_id
        self.seed = seed
        self.fmap = fmap
        self.ppy = periods_per_year
        self.param_only_max = param_only_max
        self.run_id = run_id
        self.config = config or GrammarConfig()
        self.rng = np.random.default_rng(rng_seed)
        self.islands = island_names()
        self.seeding = {
            i: GrammarConfig(
                clause_count_weights=self.config.clause_count_weights,
                or_probability=self.config.or_probability,
                take_profit_probability=self.config.take_profit_probability,
                max_params=self.config.max_params,
                clause_types=_clause_types(island_category(i)),
            )
            for i in self.islands
        }
        # resume: counts and what was already proposed come from the ledger
        self.proposals = 0
        self.children = 0
        self.param_only = 0
        self.seen: set[tuple[str, tuple[tuple[str, float | int], ...]]] = set()
        for _event, _island, detail in self._submissions():
            self.proposals += 1
            genome = detail.get("descriptors", {}).get("genome")
            source = None
            if genome is not None:
                source, params = render_genome(load_genome(genome))
                self.seen.add((source, tuple(sorted(params.items()))))
            if detail.get("mutation"):
                self.children += 1
                parents = tuple(str(p) for p in detail.get("parents", ()))
                self.param_only += source is not None and changed_params_only(source, parents)

    # ── ledger state ────────────────────────────────────────────────────────────────────
    def _submissions(self) -> list[tuple[str, str | None, Mapping[str, Any]]]:
        return [
            (e, i, d)
            for e, i, d in self.ledger.events_for(self.campaign_id, engine=ENGINE, seed=self.seed)
            if e == Event.CANDIDATE_SUBMITTED and d
        ]

    def _genomes(self) -> dict[str, Genome]:
        out: dict[str, Genome] = {}
        for _e, _i, d in self._submissions():
            g = d.get("descriptors", {}).get("genome")
            if g is not None:
                out[str(d["candidate_id"])] = load_genome(g)
        return out

    def _state(self) -> tuple[dict[str, list[Entry]], dict[str, float], dict[str, Genome]]:
        entries = load_entries(self.ledger, self.campaign_id, ENGINE, self.seed, self.fmap)
        stats = self.ledger.trial_stats()
        ctx = RankContext(max(stats.n_eff, 1), stats.var_sr or 0.0, self.ppy)
        score = scores(entries, ctx)
        moved = migrations(self.ledger, self.campaign_id, ENGINE, self.seed)
        return populations(entries, moved, self.islands), score, self._genomes()

    # ── one proposal ────────────────────────────────────────────────────────────────────
    def _fresh(self, island: str) -> Proposal:
        genome = sample_genome(self.rng, self.seeding[island])
        source, params = render_genome(genome)
        return Proposal(genome, source, params, island=island)

    def _bred(
        self,
        island: str,
        pop: list[Entry],
        score: Mapping[str, float],
        genomes: Mapping[str, Genome],
    ) -> Proposal | None:
        pop = [e for e in pop if e.candidate_id in genomes]
        elites = [e for e in island_archive(pop, score).elites() if e.candidate_id in genomes]
        parent = sample_parent(self.rng, elites, pop)
        if parent is None:
            return None
        kind: MutationKind = choose_kind(
            self.rng, self.children, self.param_only, self.param_only_max
        )
        mate = sample_mate(self.rng, parent, elites, pop) if kind == "crossover" else None
        if kind == "crossover" and mate is None:
            kind = "subtree"
        try:
            child = breed(
                kind, genomes[parent.candidate_id], self.rng, self.config,
                other=genomes[mate.candidate_id] if mate is not None else None,
            )  # fmt: skip
        except OperatorFailed:
            return None
        source, params = render_genome(child)
        parents = (parent.strategy_hash,) + ((mate.strategy_hash,) if mate is not None else ())
        return Proposal(child, source, params, parents=parents, mutation=kind, island=island)

    def next(self) -> Proposal:
        island = self.islands[self.proposals % len(self.islands)]
        generation = self.proposals // len(self.islands)
        pops, score, genomes = self._state()
        if (
            self.proposals % len(self.islands) == 0
            and generation > 0
            and generation % MIGRATION_INTERVAL == 0
        ):
            for m in plan_migration(pops, score):
                record_migration(self.ledger, self.campaign_id, ENGINE, self.seed, self.run_id, m)
            pops, score, genomes = self._state()
        for attempt in range(MAX_TRIES):
            proposal = (
                self._fresh(island)  # last resorts: a fresh genome is never a capped child
                if attempt >= MAX_TRIES - 2
                else self._bred(island, pops[island], score, genomes) or self._fresh(island)
            )
            accepted = self._accept(proposal)
            if accepted is not None:
                return accepted
        raise RuntimeError(f"island {island}: no new proposal after {MAX_TRIES} tries")

    def _accept(self, proposal: Proposal) -> Proposal | None:
        """Take this proposal unless it repeats one already made, or unless it is a child that
        renders to its parent's code while the parameter-only share is already at the cap
        (D20, INV-75). The drawn operator is not evidence: only the rendered child is."""
        key = (proposal.source, tuple(sorted(proposal.params.items())))
        if key in self.seen:
            return None
        param_only = proposal.mutation is not None and changed_params_only(
            proposal.source, proposal.parents
        )
        if param_only and self.param_only + 1 > self.param_only_max * (self.children + 1):
            return None
        self.seen.add(key)
        self.proposals += 1
        if proposal.mutation is not None:
            self.children += 1
            self.param_only += param_only
        return proposal
