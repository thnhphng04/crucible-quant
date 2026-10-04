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


def test_an_atr_stop_period_is_a_tunable_and_the_renders_only_atr() -> None:
    """INV-115, as ADR-0047 amends it: the stop and the guard read ATR(n_stop), the only ATR;
    an ATR-scaled Distance keeps its own ATR(14) beside the stop's ATR(n_stop)."""
    g = _with_stop_period("atr")
    src, params = genome.render_genome(g, "long", tp_sl_ratio=1.1)
    assert params == {"n1": 20, "k_stop": 2.0, "n_stop": 21}
    assert "# TUNABLE: n_stop = 21, bounds=(5, 50)" in src
    assert src.index("# TUNABLE: k_stop") < src.index("# TUNABLE: n_stop")
    assert '"atr": ind.atr(bars, self.p.n_stop),' in src
    assert "atr_stop" not in src and "ind.atr(bars, 14)" not in src
    assert "stop = self.p.k_stop * atr\n" in src
    assert "ready = atr > 0 and atr - atr == 0 and stop > 0" in src
    assert genome.feature_specs(g, "long", 1.1)[-1:] == [("atr", "ind.atr(bars, self.p.n_stop)")]
    k = genome.Param("level", -3.0, 3.0, 1.0)
    dist = genome.Distance(genome.Close(), genome.Indicator("sma", genome.Param("period", 2, 300, 20)),
                           ">", k)  # fmt: skip
    with_distance = dataclasses.replace(g, entry=dist)
    assert genome.feature_specs(with_distance, "long", 1.1)[-2:] == [
        ("atr_stop", "ind.atr(bars, self.p.n_stop)"),
        ("atr", "ind.atr(bars, 14)"),
    ]
    src2, _ = genome.render_genome(with_distance, "long", tp_sl_ratio=1.1)
    assert 'stop = self.p.k_stop * x["atr_stop"]' in src2


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


def _spread_distance(left: genome.Series, right: genome.Series) -> genome.Genome:
    return genome.Genome(
        genome.Distance(left, right, ">", genome.Param("level", -3.0, 3.0, 1.5), scale="spread"),
        genome.Param("mult", 0.5, 5.0, 2.0),
    )


def test_a_spread_distance_divides_by_the_spreads_own_deviation() -> None:
    """INV-119 (ADR-0045): (a − b) / stdev(a − b, n), n the period of b — of a when b is the
    close — read from its existing TUNABLE: no new parameter."""
    ema = genome.Indicator("ema", genome.Param("period", 2, 300, 10))
    sma = genome.Indicator("sma", genome.Param("period", 2, 300, 50))
    src, params = genome.render_genome(_spread_distance(ema, sma), "long")
    assert params == {"n1": 10, "n2": 50, "lv1": 1.5, "k_stop": 2.0}
    spec = (
        "ind.spread_stdev(ind.ema(bars.close, self.p.n1), ind.sma(bars.close, self.p.n2), "
        "self.p.n2)"
    )
    assert f'"f3": {spec},' in src
    assert '(x["f1"] - x["f2"]) / x["f3"] > self.p.lv1' in src
    src, _ = genome.render_genome(_spread_distance(sma, genome.Close()), "short")
    assert '"f3": ind.spread_stdev(ind.sma(bars.close, self.p.n1), bars.close, self.p.n1),' in src


def test_an_atr_distance_renders_and_serializes_as_before() -> None:
    """Below grammar v4 the field keeps its default: the render and the JSON are unchanged."""
    d = genome.Distance(
        genome.Close(), genome.Indicator("sma", genome.Param("period", 2, 300, 20)), "<",
        genome.Param("level", -3.0, 3.0, -1.0),
    )  # fmt: skip
    assert d.scale == "atr"
    g = genome.Genome(d, genome.Param("mult", 0.5, 5.0, 2.0))
    assert '/ x["atr"] < self.p.lv1' in genome.render_genome(g)[0]
    as_dict = genome.genome_to_dict(g)
    assert "scale" not in json.dumps(as_dict)
    assert genome.load_genome(as_dict) == g
    spread = dataclasses.replace(g, entry=dataclasses.replace(d, scale="spread"))
    assert genome.load_genome(json.loads(json.dumps(genome.genome_to_dict(spread)))) == spread


def test_the_agent_grammar_re_exports_the_same_objects() -> None:
    for name in ("Genome", "Param", "Combine", "render_genome", "load_genome", "genome_to_dict"):
        assert getattr(grammar, name) is getattr(genome, name)
