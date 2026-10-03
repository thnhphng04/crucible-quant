"""The genome types live in ``core`` so the validation and execution layers can read a genome
without importing the agent layer (P3-32, ADR-0038)."""

from __future__ import annotations

import dataclasses
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


def _with_stop_period(stop_kind: str) -> genome.Genome:
    g = genome.Genome(
        genome.Slope(genome.Indicator("ema", genome.Param("period", 2, 300, 20)), True),
        genome.Param("mult", 0.5, 5.0, 2.0),
        stop_kind=stop_kind,  # type: ignore[arg-type]
        stop_period=genome.Param("period", 5, 50, 21),
    )
    return g


def test_an_atr_stop_period_is_its_own_feature_and_tunable() -> None:
    """INV-115: the stop reads ATR(n_stop); the guard and Distance keep ATR(14)."""
    g = _with_stop_period("atr")
    src, params = genome.render_genome(g, "long", tp_sl_ratio=1.1)
    assert params == {"n1": 20, "k_stop": 2.0, "n_stop": 21}
    assert "# TUNABLE: n_stop = 21, bounds=(5, 50)" in src
    assert src.index("# TUNABLE: k_stop") < src.index("# TUNABLE: n_stop")
    assert '"atr_stop": ind.atr(bars, self.p.n_stop),' in src
    assert '"atr": ind.atr(bars, 14),' in src
    assert 'stop = self.p.k_stop * x["atr_stop"]' in src
    assert "ready = atr > 0 and atr - atr == 0 and stop > 0" in src
    assert genome.feature_specs(g, "long", 1.1)[-2:] == [
        ("atr_stop", "ind.atr(bars, self.p.n_stop)"),
        ("atr", "ind.atr(bars, 14)"),
    ]


def test_a_bollinger_stop_period_is_the_band_period() -> None:
    g = _with_stop_period("bollinger")
    for direction, band in (("long", "boll_lower"), ("short", "boll_upper")):
        src, params = genome.render_genome(g, direction, tp_sl_ratio=1.1)  # type: ignore[arg-type]
        assert params["n_stop"] == 21
        assert f"ind.{band}(bars.close, self.p.n_stop)" in src
        assert "atr_stop" not in src and ", 8)" not in src


def test_a_genome_without_stop_period_serializes_as_before() -> None:
    """INV-114: the old JSON form neither gains a key nor fails to load."""
    g = dataclasses.replace(_with_stop_period("atr"), stop_period=None)
    d = genome.genome_to_dict(g)
    assert isinstance(d, dict) and "stop_period" not in d
    assert genome.load_genome(d) == g
    g7 = _with_stop_period("bollinger")
    assert genome.load_genome(json.loads(json.dumps(genome.genome_to_dict(g7)))) == g7


def test_the_agent_grammar_re_exports_the_same_objects() -> None:
    for name in ("Genome", "Param", "Combine", "render_genome", "load_genome", "genome_to_dict"):
        assert getattr(grammar, name) is getattr(genome, name)
