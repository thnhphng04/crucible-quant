"""`direction` as a grammar axis (P3-04, ADR-0031) — INV-91.

A scope searches one side. The genome carries which side it is, the renderer emits exactly that
side, and gate ①a refuses a strategy whose source can emit the other one. Under the retired
long-or-flat grammar the renderer hardcoded `Signal("long", …)`, so a short scope had no way to
exist.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from quantcrucible.agent.engines.gp_search import GpSearch
from quantcrucible.agent.engines.random_search import RandomSearch
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.grammar import GrammarConfig, render_genome
from quantcrucible.core.strategy.base import Bars, generate_signals
from quantcrucible.core.strategy.template import load_strategy_class
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.gates import strategy_hash
from quantcrucible.validation.run import FEATURE_MAP
from tests.factories import unit


def _bars(n: int = 300, seed: int = 0) -> Bars:
    rng = np.random.default_rng(seed)
    close = np.asarray(100 * np.cumprod(1 + rng.normal(0.0002, 0.02, n)), dtype=np.float64)
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


def test_both_engines_render_the_configured_side(tmp_path: Path) -> None:
    """The engines, not just the renderer: a short scope's proposals emit short or flat."""
    config = GrammarConfig(direction="short", tp_sl_ratio=1.1, boll_stop_probability=0.5)
    random = RandomSearch(seed=3, config=config)
    for _ in range(20):
        source = random.next().source
        assert 'Signal("short"' in source and 'Signal("long"' not in source
    lg = Ledger.open(tmp_path / "l.db")
    lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
    fmap = FeatureMap.from_lock({"derived": {"feature_map": FEATURE_MAP}})
    key = unit(instrument="BTC/USDT", direction="short")
    gp = GpSearch(lg, "c1", key, 7, fmap, 365.0, 0.30, "run", config=config)
    for _ in range(20):
        source = gp.next().source
        assert 'Signal("short"' in source and 'Signal("long"' not in source
