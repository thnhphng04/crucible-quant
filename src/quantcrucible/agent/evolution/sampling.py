"""Parent and mate sampling for engine C-gp (arch §3.1.4, Eq. 1, P2-12).

Within the island being processed: with probability ``alpha`` a parent is drawn uniformly from
the island's feature-map elites (exploitation), otherwise uniformly from its whole population
(exploration). The mate for crossover — the GP counterpart of the paper's cousins — is drawn the
same way from the same island, never the parent itself when another entry exists. An empty
island yields no parent.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from quantcrucible.agent.evolution.archive import Entry

ALPHA = 0.5  # arch §3.1.8


def sample_parent(
    rng: np.random.Generator,
    elites: Sequence[Entry],
    population: Sequence[Entry],
    alpha: float = ALPHA,
) -> Entry | None:
    if not population:
        return None
    pool = elites if elites and rng.random() < alpha else population
    return pool[int(rng.integers(len(pool)))]


def sample_mate(
    rng: np.random.Generator,
    parent: Entry,
    elites: Sequence[Entry],
    population: Sequence[Entry],
    alpha: float = ALPHA,
) -> Entry | None:
    others = [e for e in population if e.candidate_id != parent.candidate_id]
    if not others:
        return None
    other_elites = [e for e in elites if e.candidate_id != parent.candidate_id]
    return sample_parent(rng, other_elites, others, alpha)
