"""The genome types live in ``core`` so the validation and execution layers can read a genome
without importing the agent layer (P3-32, ADR-0038)."""

from __future__ import annotations

import hashlib
import json

import numpy as np

from quantcrucible.agent import grammar
from quantcrucible.core.strategy import genome

# SHA-256 over 1,000 seeded genomes rendered long/short, with and without a TP ratio, plus their
# JSON form — computed on the code as it stood before the move (P3-32).
GOLDEN = "56ecf395eb8f56aeee7fccff62f5da95b4cc9a49f6fe7225e2b0c3f37cc9b01b"


def test_rendering_and_serialization_are_unchanged_by_the_move() -> None:
    h = hashlib.sha256()
    cfg = grammar.GrammarConfig(take_profit_probability=0.5, boll_stop_probability=0.5)
    rng = np.random.default_rng(20260930)
    for _ in range(1000):
        g = grammar.sample_genome(rng, cfg)
        for direction in ("long", "short"):
            for ratio in (None, 1.1):
                src, params = genome.render_genome(g, direction, tp_sl_ratio=ratio)
                h.update(src.encode())
                h.update(json.dumps(params, sort_keys=True).encode())
        d = genome.genome_to_dict(g)
        h.update(json.dumps(d, sort_keys=True).encode())
        assert genome.load_genome(json.loads(json.dumps(d))) == g
    assert h.hexdigest() == GOLDEN


def test_the_agent_grammar_re_exports_the_same_objects() -> None:
    for name in ("Genome", "Param", "Combine", "render_genome", "load_genome", "genome_to_dict"):
        assert getattr(grammar, name) is getattr(genome, name)
