"""Grammar v5: the volatility clauses VolRatio and Bandwidth (P3-61, INV-121, ADR-0047)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from quantcrucible.agent.engines.gp_search import _seeding
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.evolution.islands import island_category, island_names
from quantcrucible.agent.evolution.operators import OperatorFailed, _flip, breed, mutate_param
from quantcrucible.agent.grammar import (
    BANDWIDTH_K_RANGE,
    CATEGORIES,
    CLAUSE_TYPES,
    CLAUSE_TYPES_V5,
    VOL_FAST_RANGE,
    VOL_RATIO_K_RANGE,
    VOL_SLOW_RANGE,
    Bandwidth,
    Genome,
    GrammarConfig,
    Param,
    VolRatio,
    categories,
    categories_for,
    clause_category,
    clause_types_for,
    render_genome,
    sample_clause,
    sample_genome,
    signature,
)
from quantcrucible.agent.run import grammar_config
from quantcrucible.core.strategy.base import Bars
from quantcrucible.core.strategy.genome import Close, Distance, Indicator
from quantcrucible.core.strategy.template import load_strategy_class, template_hash
from quantcrucible.core.strategy.tunable import STOP_PERIOD_TUNABLE
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.backtest_report import indicator_corr
from quantcrucible.validation.gates import GateContext, StrategyCandidate
from quantcrucible.validation.guardrail import StaticGuardrail
from quantcrucible.validation.run import FEATURE_MAP_V2
from tests.factories import unit

V5 = GrammarConfig(
    stop_period=True, max_params=7, version=5, tp_sl_ratio=1.1, clause_types=CLAUSE_TYPES_V5
)


def _bars(n: int, seed: int) -> Bars:
    """A regime-switching walk with intrabar ranges: TR spikes drive every ATR at once."""
    rng = np.random.default_rng(seed)
    regime = np.repeat(rng.choice([-1.0, 0.0, 1.0], size=n // 80 + 1), 80)[:n]
    vol = np.repeat(rng.uniform(0.005, 0.03, size=n // 150 + 1), 150)[:n]
    close = 100 * np.exp(np.cumsum(rng.normal(regime * 0.002, vol)))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, vol / 2)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, vol / 2)))
    ts = np.datetime64("2020-01-01", "ns") + np.arange(1, n + 1) * np.timedelta64(1, "h")
    return Bars("X/USDT", "1h", ts, open_, high, low, close, np.ones(n))


def test_an_atr_stop_with_its_period_gene_is_the_renders_only_atr() -> None:
    """ADR-0047: ATR(14) beside ATR(n_stop) moved with it (|ρ| of changes 0.94–0.996 on IS
    bars), failing gate ③ for 39 of 40 genomes; the guard now reads the stop's own ATR. An
    ATR-scaled Distance — not sampled since v4 — keeps ATR(14), as ADR-0041 set."""
    c = sample_clause(np.random.default_rng(0), VolRatio, 5)
    g = Genome(c, Param("mult", 0.5, 5.0, 1.5), stop_period=Param("period", 5, 50, 20))
    src, _ = render_genome(g, "long", 1.1)
    assert g.one_atr and '"atr": ind.atr(bars, self.p.n_stop)' in src
    assert "atr_stop" not in src and "ind.atr(bars, 14)" not in src
    assert "stop = self.p.k_stop * atr\n" in src
    atr_distance = Distance(Close(), Indicator("sma", Param("period", 2, 300, 20)), ">",
                            Param("level", -3.0, 3.0, 1.0))  # fmt: skip
    two = Genome(atr_distance, g.stop, stop_period=g.stop_period)
    src2, _ = render_genome(two, "long", 1.1)
    assert not two.one_atr and '"atr_stop"' in src2 and '"atr": ind.atr(bars, 14)' in src2
    fixed, _ = render_genome(Genome(c, g.stop), "long", 1.1)  # no period gene: ATR(14), as ever
    assert not Genome(c, g.stop).one_atr and '"atr": ind.atr(bars, 14)' in fixed


def test_v5_genomes_rarely_fail_the_indicator_correlation_and_never_on_an_atr() -> None:
    """Gate ③'s max_indicator_corr 0.9 compares every two features' changes: the stop's ATR
    is alone, and a volatility ratio is one feature, so no ATR pair is left to fail it."""
    bars = _bars(3_000, 3)
    rng = np.random.default_rng(6)
    over = 0
    for i in range(120):
        src, params = render_genome(sample_genome(rng, V5), "long", 1.1)
        strategy = load_strategy_class(src, f"v5_corr_{i}")(params)
        corr, pair = indicator_corr([strategy.indicators(bars)])
        if corr > 0.9:
            over += 1
            assert pair is not None and not any(p.startswith("atr") for p in pair), pair
    assert over < 120 * 0.15


@pytest.mark.parametrize("kind", [VolRatio, Bandwidth])
def test_a_volatility_clause_has_disjoint_windows_and_its_own_k_range(kind: type) -> None:
    """The fast window ends below the slow one starts, so no ``param`` move can swap them."""
    k_range = VOL_RATIO_K_RANGE if kind is VolRatio else BANDWIDTH_K_RANGE
    rng = np.random.default_rng(0)
    for _ in range(300):
        c = sample_clause(rng, kind, 5)
        assert isinstance(c, kind) and isinstance(c, VolRatio | Bandwidth)
        assert (c.fast.low, c.fast.high) == VOL_FAST_RANGE
        assert (c.slow.low, c.slow.high) == VOL_SLOW_RANGE
        assert c.fast.value < c.slow.value
        assert (c.k.low, c.k.high) == k_range and k_range[0] <= c.k.value <= k_range[1]


def test_only_a_v5_campaign_samples_the_volatility_clauses() -> None:
    """Older versions keep the clause stream they always drew (INV-121)."""
    assert clause_types_for(4) == CLAUSE_TYPES == GrammarConfig().clause_types
    assert clause_types_for(5) == (*CLAUSE_TYPES, VolRatio, Bandwidth)
    old = {"research": {"exit": {"tp_sl_ratio": 1.1}}, "derived": {"grammar_version": 4}}
    new = {**old, "derived": {"grammar_version": 5}}
    assert grammar_config(old, unit()).clause_types == CLAUSE_TYPES
    assert grammar_config(new, unit()).clause_types == CLAUSE_TYPES_V5
    rng = np.random.default_rng(2)
    kinds = {type(c) for _ in range(400) for c in sample_genome(rng, V5).clauses()}
    assert {VolRatio, Bandwidth} <= kinds


@pytest.mark.parametrize("direction", ["long", "short"])
def test_a_volatility_clause_is_its_own_category_on_either_side(direction: str) -> None:
    c = sample_clause(np.random.default_rng(1), VolRatio, 5)
    assert clause_category(c, direction, 5) == "volatility"  # type: ignore[arg-type]
    g = Genome(c, Param("mult", 0.5, 5.0, 1.0))
    assert categories(g, direction, 5) == ("volatility",)  # type: ignore[arg-type]


def test_v5_adds_a_sixth_island_and_keeps_older_campaigns_at_five() -> None:
    """N = C + 1: the volatility island is i4 and the open one i5 under v5; under v4, i4 is
    still the open island, so an older campaign resumes on the islands it ran on."""
    assert categories_for(4) == CATEGORIES and len(island_names(4)) == 5
    assert categories_for(5) == (*CATEGORIES, "volatility")
    assert island_names(5) == ("i0", "i1", "i2", "i3", "i4", "i5")
    assert island_category("i4", 5) == "volatility" and island_category("i5", 5) is None
    assert island_category("i4", 4) is None


@pytest.mark.parametrize("direction", ["long", "short"])
def test_the_volatility_island_seeds_only_volatility_clauses(direction: str) -> None:
    cfg = _seeding(
        GrammarConfig(direction=direction, version=5, clause_types=CLAUSE_TYPES_V5),  # type: ignore[arg-type]
        "volatility",
    )
    assert cfg.clause_types == (VolRatio, Bandwidth)
    rng = np.random.default_rng(4)
    for _ in range(100):
        assert categories(sample_genome(rng, cfg), direction, 5) == ("volatility",)  # type: ignore[arg-type]
    assert _seeding(cfg, None).clause_types == CLAUSE_TYPES_V5  # the open island
    assert VolRatio not in _seeding(cfg, "mean_reversion").clause_types


def test_the_new_feature_map_bins_the_volatility_category() -> None:
    fmap = FeatureMap.from_lock({"derived": {"feature_map": FEATURE_MAP_V2}})
    assert fmap.category_bits(["trend", "volatility"]) == 0b10001


def test_operators_keep_a_volatility_clause_inside_its_bounds() -> None:
    """A point move flips the comparison and keeps k (its range holds on both sides); a
    param move keeps each window in its own range."""
    rng = np.random.default_rng(5)
    c = sample_clause(rng, Bandwidth, 5)
    assert isinstance(c, Bandwidth)
    flipped = _flip(c, rng)
    assert isinstance(flipped, Bandwidth) and flipped.op != c.op and flipped.k == c.k
    g = Genome(c, Param("mult", 0.5, 5.0, 1.0))
    for _ in range(300):
        try:
            g = mutate_param(g, rng)
        except OperatorFailed:
            continue
        b = g.entry
        assert isinstance(b, Bandwidth)
        assert VOL_FAST_RANGE[0] <= b.fast.value <= VOL_FAST_RANGE[1] < b.slow.value
        assert b.slow.value <= VOL_SLOW_RANGE[1]
    assert signature(g) == (f"bandwidth({c.op})",)


@pytest.fixture
def ctx(tmp_path: Path) -> GateContext:
    lock = {
        "research": {"evolve_scope": "joint", "exit": {"tp_sl_ratio": 1.1, "stop_kinds": ["atr"]}},
        "derived": {"template_hash": template_hash("joint"), "exit_protocol": "bracket_timeout_v1",
                    "stop_period": STOP_PERIOD_TUNABLE, "max_tunables": 7, "grammar_version": 5},
    }  # fmt: skip
    return GateContext(ledger=Ledger.open(tmp_path / "l.db"), lock=lock)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_v5_genomes_and_their_children_pass_gate_1a(ctx: GateContext, direction: str) -> None:
    """ind.bandwidth is whitelisted, and atr / atr and bandwidth / bandwidth are dimensionless
    against a declared level: every v5 genome and child passes ①a by construction."""
    rng = np.random.default_rng(1)
    pool = [sample_genome(rng, V5) for _ in range(60)]
    genomes = list(pool)
    for i in range(240):
        kind = ("param", "point", "subtree", "crossover")[i % 4]
        a, b = pool[int(rng.integers(len(pool)))], pool[int(rng.integers(len(pool)))]
        try:
            genomes.append(breed(kind, a, rng, V5, other=b))  # type: ignore[arg-type]
        except OperatorFailed:
            continue
    ratios = 0
    for g in genomes:
        src, params = render_genome(g, direction, 1.1)  # type: ignore[arg-type]
        ratios += "ind.band_ratio(" in src or "ind.atr_ratio(" in src
        cand = StrategyCandidate(
            candidate_id="c", source=src, params=params, universe=("BTC/USDT",), timeframe="1h",
            timerange="x", run_id="r", campaign_id="c1", direction=direction,  # type: ignore[arg-type]
        )  # fmt: skip
        result = StaticGuardrail().check(cand, ctx)
        assert result.passed, f"{result.reason}\n{src}"
    assert ratios > 30
