"""Strategy categories by direction (P3-56, INV-117, ADR-0043).

A clause has a family (trend, momentum, breakout) and a polarity: +1 when it holds as price rises.
Its category is the family when polarity × side is positive, mean reversion otherwise. Two-series
clauses are oriented fast − slow first, so two spellings of one condition share a label. The
expected labels below are written by hand from those rules, not read back from the code.
"""

from __future__ import annotations

import numpy as np
import pytest

from quantcrucible.agent.grammar import (
    CATEGORIES,
    CATEGORY_CLAUSES_V2,
    Breakout,
    Clause,
    Close,
    Compare,
    Cross,
    CrossLevel,
    Distance,
    Genome,
    GrammarConfig,
    Indicator,
    Param,
    Slope,
    Threshold,
    categories,
    clause_category,
    sample_genome,
)


def ind(op: str, n: int) -> Indicator:
    return Indicator(op, Param("period", 2, 300, n))


def lv(v: float) -> Param:
    return Param("level", -100, 100, v)


CLOSE = Close()
T, M, B, MR = "trend", "momentum", "breakout", "mean_reversion"

# (clause, long, short)
TABLE: list[tuple[Clause, str, str]] = [
    (Compare(CLOSE, ">", ind("sma", 20)), T, MR),  # price above its average
    (Compare(CLOSE, "<", ind("sma", 20)), MR, T),
    (Compare(ind("sma", 50), ">", ind("ema", 10)), MR, T),  # slow above fast: a downtrend
    (Compare(ind("ema", 20), ">", ind("sma", 20)), T, MR),  # same period: ema is the faster
    (Cross(ind("ema", 10), True, ind("sma", 50)), T, MR),  # golden cross
    (Cross(ind("sma", 50), True, ind("ema", 10)), MR, T),  # slow crossing above fast
    (Cross(CLOSE, False, ind("sma", 30)), MR, T),
    (Distance(CLOSE, ind("sma", 50), ">", lv(1.0)), T, MR),
    (Distance(ind("sma", 50), CLOSE, "<", lv(-1.0)), T, MR),  # the same condition, respelled
    (Distance(CLOSE, ind("sma", 50), "<", lv(-2.0)), MR, T),  # stretched below its average
    (Threshold(ind("rsi", 14), ">", lv(70)), M, MR),
    (Threshold(ind("rsi", 14), "<", lv(30)), MR, M),
    (Threshold(ind("zscore", 20), ">", lv(1.0)), M, MR),
    (CrossLevel(ind("rsi", 14), True, lv(60)), M, MR),
    (CrossLevel(ind("rsi", 14), False, lv(30)), MR, M),
    (Breakout(True, Param("period", 2, 300, 20)), B, MR),
    (Breakout(False, Param("period", 2, 300, 20)), MR, B),  # a short breakdown is a breakout
    (Slope(ind("ema", 50), True), T, MR),
    (Slope(ind("ema", 50), False), MR, T),
    (Slope(ind("rsi", 14), True), M, MR),
    (Slope(ind("rsi", 14), False), MR, M),
]


@pytest.mark.parametrize(("clause", "long", "short"), TABLE)
def test_version_2_labels_by_direction(clause: Clause, long: str, short: str) -> None:
    assert clause_category(clause, "long", 2) == long
    assert clause_category(clause, "short", 2) == short


# version 1: the label of the clause alone, whatever the side (campaigns locked before P3-56)
V1: list[tuple[Clause, str]] = [
    (Compare(CLOSE, "<", ind("sma", 20)), T),
    (Cross(ind("sma", 50), True, ind("ema", 10)), T),
    (Distance(ind("sma", 50), CLOSE, "<", lv(-1.0)), MR),
    (Threshold(ind("rsi", 14), ">", lv(70)), M),
    (Breakout(False, Param("period", 2, 300, 20)), MR),
    (Slope(ind("ema", 50), False), T),
    (Slope(ind("rsi", 14), False), M),
]


@pytest.mark.parametrize(("clause", "label"), V1)
def test_version_1_is_unchanged_and_ignores_the_side(clause: Clause, label: str) -> None:
    assert clause_category(clause) == label
    assert clause_category(clause, "short", 1) == label


def test_a_genomes_categories_follow_the_side() -> None:
    stop = Param("mult", 0.5, 5.0, 2.0)
    g = Genome(Threshold(ind("rsi", 14), "<", lv(30)), stop)
    assert categories(g) == (MR,)
    assert categories(g, "long", 2) == (MR,)
    assert categories(g, "short", 2) == (M,)


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("category", CATEGORIES)
def test_a_version_2_island_seeds_only_its_own_category(direction: str, category: str) -> None:
    """Under v1 an island got its category's clause *types*, half of which mean the opposite
    style once the side or the operator flips; under v2 every seeded clause has the category."""
    cfg = GrammarConfig(
        direction=direction,  # type: ignore[arg-type]
        version=2,
        category=category,
        clause_types=CATEGORY_CLAUSES_V2[category],
    )
    rng = np.random.default_rng(3)
    for _ in range(150):
        g = sample_genome(rng, cfg)
        assert categories(g, direction, 2) == (category,), g  # type: ignore[arg-type]


def test_a_version_1_config_samples_exactly_as_before() -> None:
    """The category field is ignored below v2: the same seed draws the same genomes."""
    a = [sample_genome(np.random.default_rng(9), GrammarConfig()) for _ in range(1)]
    b = [
        sample_genome(np.random.default_rng(9), GrammarConfig(category="breakout"))
        for _ in range(1)
    ]
    assert a == b


def test_gp_islands_seed_by_category_only_from_version_2() -> None:
    from quantcrucible.agent.engines.gp_search import _seeding
    from quantcrucible.agent.grammar import CATEGORY_CLAUSES, CLAUSE_TYPES

    v1 = _seeding(GrammarConfig(direction="short"), "momentum")
    assert (v1.category, v1.clause_types) == (None, CATEGORY_CLAUSES["momentum"])
    v2 = _seeding(GrammarConfig(direction="short", version=2), "mean_reversion")
    assert (v2.category, v2.clause_types) == ("mean_reversion", CLAUSE_TYPES)
    assert _seeding(GrammarConfig(version=2), None).category is None  # the open island


def test_the_lock_names_the_grammar_version() -> None:
    from quantcrucible.agent.run import grammar_config
    from quantcrucible.core.strategy.tunable import GRAMMAR_VERSION, lock_grammar_version
    from tests.factories import unit

    old = {"research": {"exit": {"tp_sl_ratio": 1.1}}, "derived": {}}
    new = {**old, "derived": {"grammar_version": GRAMMAR_VERSION}}
    assert lock_grammar_version(old) == 1
    assert lock_grammar_version(new) == GRAMMAR_VERSION >= 2  # later tasks raise it
    assert grammar_config(old, unit()).version == 1
    assert grammar_config(new, unit()).version == GRAMMAR_VERSION
