"""Recovering a genome from its rendered source, without running it (P3-33, INV-106, ADR-0038).

The kernel engine reads a genome only when re-rendering it reproduces the ledgered source byte
for byte, so the genome it interprets is exactly the strategy the ledger recorded. Everything
else is refused and takes the sandbox path.
"""

from __future__ import annotations

import ast
import builtins
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from quantcrucible.agent.evolution.operators import OperatorFailed, breed
from quantcrucible.agent.grammar import GrammarConfig, sample_genome
from quantcrucible.core.strategy import template
from quantcrucible.core.strategy.genome import render_genome
from quantcrucible.core.strategy.genome_parse import (
    MAX_SOURCE_BYTES,
    GenomeParseError,
    parse_genome,
)

ZOO = Path(template.__file__).resolve().parents[1] / "zoo"
CFG = GrammarConfig(take_profit_probability=0.5, boll_stop_probability=0.5)
# a stop_period: tunable_v1 campaign (ADR-0041)
CFG_STOP = GrammarConfig(
    take_profit_probability=0.5, boll_stop_probability=0.5, stop_period=True, max_params=7
)
# grammar v4: Distance divides by the spread's own deviation (ADR-0045)
CFG_V4 = GrammarConfig(stop_period=True, max_params=7, version=4)
CFGS = pytest.mark.parametrize(
    "cfg", [CFG, CFG_STOP, CFG_V4], ids=["fixed-stop", "stop-period", "grammar-v4"]
)


def _sources(
    n: int, seed: int = 7, cfg: GrammarConfig = CFG
) -> list[tuple[Any, str, float | None, str]]:
    rng = np.random.default_rng(seed)
    out = []
    for _ in range(n):
        g = sample_genome(rng, cfg)
        for direction in ("long", "short"):
            for ratio in (None, 1.1):
                out.append((g, direction, ratio, render_genome(g, direction, ratio)[0]))
    return out


@CFGS
def test_every_rendered_genome_parses_back_to_itself(cfg: GrammarConfig) -> None:
    for g, direction, ratio, src in _sources(500, cfg=cfg):
        parsed = parse_genome(src)
        assert parsed.genome == g
        assert parsed.direction == direction
        assert parsed.tp_sl_ratio == ratio


@CFGS
def test_bred_children_parse_back_to_themselves(cfg: GrammarConfig) -> None:
    """C-gp offspring can share a parameter object between two nodes; one TUNABLE, one object."""
    rng = np.random.default_rng(1)
    parsed = 0
    for _ in range(150):
        a, b = sample_genome(rng, cfg), sample_genome(rng, cfg)
        for kind in ("param", "point", "subtree", "crossover"):
            try:
                child = breed(kind, a, rng, cfg, other=b)
            except OperatorFailed:
                continue
            src, _ = render_genome(child, "long", 1.1)
            assert parse_genome(src).genome == child
            parsed += 1
    assert parsed > 500


def test_parsing_never_executes_the_source(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("candidate source was executed")

    monkeypatch.setattr(builtins, "exec", forbidden)
    monkeypatch.setattr(builtins, "eval", forbidden)
    monkeypatch.setattr(template, "load_strategy_class", forbidden)
    for _g, _d, _r, src in _sources(20, seed=11):
        parse_genome(src)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("self.p.k_stop *", "self.p.k_stop * 2 *"),  # extra arithmetic on the stop
        ("atr > 0 and", "atr >= 0 and"),  # a weakened readiness guard
        ('"atr": ind.atr(bars, 14)', '"atr": ind.atr(bars, 15)'),  # a different ATR period
        ('return Signal("flat", 0.0, 0.0)', 'return Signal("flat", 0.0, 0.0)  # ok'),
        ("    def signal", "    # note\n    def signal"),  # any byte outside the render
    ],
)
def test_a_source_that_is_not_an_exact_render_is_refused(old: str, new: str) -> None:
    _g, _d, _r, src = _sources(1, seed=3)[0]
    assert old in src
    with pytest.raises(GenomeParseError):
        parse_genome(src.replace(old, new, 1))


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("ind.atr(bars, self.p.n_stop)", "ind.atr(bars, 14)"),  # the gene's feature unbound
        ('self.p.k_stop * x["atr_stop"]', 'self.p.k_stop * x["atr"]'),  # the stop off its gene
    ],
)
def test_a_stop_period_render_edited_is_refused(old: str, new: str) -> None:
    for _g, _d, _r, src in _sources(50, seed=13, cfg=CFG_STOP):
        if old in src:
            with pytest.raises(GenomeParseError):
                parse_genome(src.replace(old, new, 1))
            return
    pytest.fail("no ATR-stop genome sampled")


def test_a_swapped_parameter_reference_is_refused() -> None:
    for _g, _d, _r, src in _sources(50, seed=5):
        if "self.p.n2" in src:
            with pytest.raises(GenomeParseError):
                parse_genome(src.replace("self.p.n2", "self.p.n1", 1))
            return
    pytest.fail("no two-period genome sampled")


@pytest.mark.parametrize("path", sorted(ZOO.glob("*.py")), ids=lambda p: p.name)
def test_hand_written_strategies_are_not_genomes(path: Path) -> None:
    if path.name == "__init__.py":
        return
    with pytest.raises(GenomeParseError):
        parse_genome(path.read_text("utf-8"))


@pytest.mark.parametrize(
    "src",
    [
        "",
        "def f(:\n",
        "x = 1\n",
        "# ═══ EVOLVE-BLOCK-START ═══\n" + "(" * 5000 + "\n# ═══ EVOLVE-BLOCK-END ═══\n",
    ],
)
def test_malformed_input_is_refused_not_raised(src: str) -> None:
    with pytest.raises(GenomeParseError):
        parse_genome(src)


def test_oversized_source_is_refused_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    _g, _d, _r, src = _sources(1)[0]

    def forbidden(*_a: object, **_k: object) -> None:
        raise AssertionError("an oversized source reached the parser")

    monkeypatch.setattr(ast, "parse", forbidden)
    with pytest.raises(GenomeParseError, match="bytes"):
        parse_genome(src + "#" * MAX_SOURCE_BYTES)
