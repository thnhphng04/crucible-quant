"""Engine C-random (arch §3.1.11): i.i.d. samples from the typed grammar — the control arm.

It reads no results: it is built from a seed and the grammar's distribution only, and never
sees the ledger, a metric or another engine's archive (INV-65). Same seed ⇒ same sequence.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from quantcrucible.agent.grammar import Genome, GrammarConfig, render_genome, sample_genome

ENGINE = "random"


@dataclass(frozen=True, slots=True)
class Proposal:
    """A candidate an engine wants evaluated: the full source and its parameter values."""

    genome: Genome
    source: str
    params: dict[str, float | int]
    parents: tuple[str, ...] = ()
    mutation: str | None = None
    island: str | None = None


class RandomSearch:
    """``next()`` returns a new proposal, never one it has already made (same code and values)."""

    engine = ENGINE

    def __init__(self, seed: int, config: GrammarConfig | None = None) -> None:
        self._rng = np.random.default_rng(seed)
        self._config = config or GrammarConfig()
        self._seen: set[tuple[str, tuple[tuple[str, float | int], ...]]] = set()

    def next(self) -> Proposal:
        while True:
            genome = sample_genome(self._rng, self._config)
            source, params = render_genome(genome)
            key = (source, tuple(sorted(params.items())))
            if key not in self._seen:
                self._seen.add(key)
                return Proposal(genome, source, params)
