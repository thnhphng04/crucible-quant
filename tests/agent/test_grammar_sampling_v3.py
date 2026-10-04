"""Grammar v3 sampling: log-uniform periods, RSI and z-score period caps (P3-57, INV-118,
ADR-0044).

P3-54 measured, on in-sample bars, which parameter ranges make a clause true on almost no bar
or almost every bar: long RSI periods squeeze RSI around 50 so its levels are never reached, a
z-score of n values never exceeds (n − 1)/√n (1.79 at n = 5), and uniform periods put two
thirds of every draw above 100 bars. The expected values here are derived from those bounds,
not read back from the sampler.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from quantcrucible.agent.evolution.operators import OperatorFailed, mutate_point
from quantcrucible.agent.grammar import (
    CLAUSE_TYPES,
    Genome,
    GrammarConfig,
    Indicator,
    Param,
    Threshold,
    period_range,
    sample_clause,
    sample_genome,
    sample_period,
)

V3 = GrammarConfig(version=3)


def _indicators(node: object) -> list[Indicator]:
    if isinstance(node, Indicator):
        return [node]
    if isinstance(node, tuple):
        return [i for x in node for i in _indicators(x)]
    fields = getattr(node, "__dataclass_fields__", None)
    if fields is None:
        return []
    return [i for f in fields for i in _indicators(getattr(node, f))]


def test_period_ranges_by_version() -> None:
    assert period_range("rsi") == (2, 100) and period_range("zscore") == (5, 300)
    assert period_range("rsi", 3) == (2, 30)
    assert period_range("zscore", 3) == (10, 300)
    assert period_range("sma", 3) == period_range("sma") == (2, 300)


def test_v3_periods_are_log_uniform_within_their_bounds() -> None:
    """P(n > 100) on [2, 300] is ln(300/100)/ln(300/2) ≈ 0.219 log-uniform, 0.67 uniform."""
    rng = np.random.default_rng(0)
    draws = [sample_period(rng, "sma", 3) for _ in range(6000)]
    assert all(p.low == 2 and p.high == 300 and 2 <= p.value <= 300 for p in draws)
    assert all(isinstance(p.value, int) for p in draws)
    share = np.mean([p.value > 100 for p in draws])
    assert share == pytest.approx(math.log(3) / math.log(150), abs=0.02)
    uniform = np.mean([sample_period(rng, "sma").value > 100 for _ in range(6000)])
    assert uniform == pytest.approx(200 / 299, abs=0.02)


def test_v3_genomes_keep_rsi_and_zscore_inside_their_caps() -> None:
    rng = np.random.default_rng(1)
    seen = set()
    for _ in range(400):
        g = sample_genome(rng, V3)
        for ind in _indicators(g.entry):
            seen.add(ind.op)
            lo, hi = period_range(ind.op, 3)
            assert (ind.period.low, ind.period.high) == (lo, hi), ind
            assert lo <= ind.period.value <= hi
    assert {"rsi", "zscore", "sma", "ema"} <= seen


@pytest.mark.parametrize("kind", CLAUSE_TYPES)
def test_v1_clause_sampling_is_the_old_stream(kind: type) -> None:
    """The version argument changes nothing below v3 (the golden renders hold too)."""
    a = sample_clause(np.random.default_rng(5), kind)
    b = sample_clause(np.random.default_rng(5), kind, 1)
    assert a == b


def test_a_v3_oscillator_swap_moves_the_period_into_the_new_range() -> None:
    """Swapping rsi(25) for a z-score must not leave a z-score of 25 bounded by rsi's [2, 30],
    nor swapping zscore(200) leave an rsi of 200."""
    stop = Param("mult", 0.5, 5.0, 2.0)
    rng = np.random.default_rng(2)
    swapped: list[Threshold] = []
    for start in (Indicator("rsi", Param("period", 2, 30, 25)),
                  Indicator("zscore", Param("period", 10, 300, 200))):  # fmt: skip
        lv = Param("level", 50, 90, 70) if start.op == "rsi" else Param("level", 0, 3, 1.5)
        g = Genome(Threshold(start, ">", lv), stop)
        for _ in range(200):
            try:
                child = mutate_point(g, rng, V3)
            except OperatorFailed:
                continue
            c = child.clauses()[0]
            if isinstance(c, Threshold) and c.osc.op != start.op:
                swapped.append(c)
    assert {c.osc.op for c in swapped} == {"rsi", "zscore"}
    for c in swapped:
        lo, hi = period_range(c.osc.op, 3)
        assert (c.osc.period.low, c.osc.period.high) == (lo, hi)
        assert lo <= c.osc.period.value <= hi
