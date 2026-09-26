"""`direction` as a grammar axis (P3-04, ADR-0031) — INV-91.

A scope searches one side. The genome carries which side it is, the renderer emits exactly that
side, and gate ①a refuses a strategy whose source can emit the other one. Under the retired
long-or-flat grammar the renderer hardcoded `Signal("long", …)`, so a short scope had no way to
exist.
"""

from __future__ import annotations

import numpy as np
import pytest

from quantcrucible.agent.engines.random_search import RandomSearch
from quantcrucible.agent.grammar import render_genome
from quantcrucible.core.strategy.base import Bars, generate_signals
from quantcrucible.core.strategy.template import load_strategy_class
from quantcrucible.validation.gates import strategy_hash


def _bars(n: int = 300, seed: int = 0) -> Bars:
    rng = np.random.default_rng(seed)
    close = 100 * np.cumprod(1 + rng.normal(0.0002, 0.02, n))
    ts = np.arange("2021-01-01", n, dtype="datetime64[D]").astype("datetime64[ns]")
    high = close * 1.01
    low = close * 0.99
    return Bars("A/USDT", "1d", ts, close, high, low, close, np.full(n, 1_000.0))


def test_a_short_genome_renders_a_short_signal() -> None:
    genome = RandomSearch(seed=3).next().genome
    long_src, _ = render_genome(genome, direction="long")
    short_src, _ = render_genome(genome, direction="short")
    assert 'Signal("long"' in long_src and 'Signal("short"' not in long_src
    assert 'Signal("short"' in short_src and 'Signal("long"' not in short_src


def test_a_long_and_a_short_rendering_have_different_hashes() -> None:
    """Same clauses, opposite sides: two distinct strategies, two distinct trials."""
    genome = RandomSearch(seed=5).next().genome
    long_src, _ = render_genome(genome, direction="long")
    short_src, _ = render_genome(genome, direction="short")
    assert strategy_hash(long_src) != strategy_hash(short_src)


def test_a_rendered_strategy_only_ever_emits_its_own_side() -> None:
    """The behavioural form of INV-91, run over real bars rather than read off the source."""
    bars = _bars()
    for seed in (1, 2, 3):
        genome = RandomSearch(seed=seed).next().genome
        for direction in ("long", "short"):
            source, params = render_genome(genome, direction=direction)
            strategy = load_strategy_class(source)(params)
            seen = {s.direction for s in generate_signals(strategy, bars, lookback=200)}
            assert seen <= {direction, "flat"}, (direction, seen)


def test_every_signal_has_strength_one() -> None:
    """ADR-0031 decision 4: `strength` must not become a second risk lever."""
    bars = _bars(seed=7)
    genome = RandomSearch(seed=11).next().genome
    source, params = render_genome(genome, direction="short")
    strategy = load_strategy_class(source)(params)
    for signal in generate_signals(strategy, bars, lookback=200):
        expected = 0.0 if signal.direction == "flat" else 1.0
        assert signal.strength == pytest.approx(expected)


def test_the_default_direction_is_long() -> None:
    """Existing callers keep the long-or-flat behaviour they had before P3-04."""
    genome = RandomSearch(seed=13).next().genome
    assert render_genome(genome)[0] == render_genome(genome, direction="long")[0]
