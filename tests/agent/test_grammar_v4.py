"""Grammar v4: Distance over its spread's own deviation (P3-58, INV-119, ADR-0045)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from quantcrucible.agent.evolution.operators import OperatorFailed, breed
from quantcrucible.agent.grammar import (
    SPREAD_OPERAND_MAX,
    Distance,
    GrammarConfig,
    Indicator,
    render_genome,
    sample_clause,
    sample_genome,
)
from quantcrucible.core.strategy.template import template_hash
from quantcrucible.core.strategy.tunable import STOP_PERIOD_TUNABLE
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.gates import GateContext, StrategyCandidate
from quantcrucible.validation.guardrail import StaticGuardrail

V4 = GrammarConfig(stop_period=True, max_params=7, version=4, tp_sl_ratio=1.1)


def test_a_v4_distance_divides_by_its_spread_and_keeps_operands_inside_the_lookback() -> None:
    """spread_stdev(a, b, n) is finite from bar max(n_a, n_b) + n − 1 of its window: operands
    ≤ 200 keep that ≤ 399, inside the locked 400-bar lookback."""
    rng = np.random.default_rng(0)
    for _ in range(300):
        c = sample_clause(rng, Distance, 4)
        assert isinstance(c, Distance) and c.scale == "spread"
        for s in (c.left, c.right):
            if isinstance(s, Indicator):
                assert (s.period.low, s.period.high) == (2, SPREAD_OPERAND_MAX)
                assert 2 <= s.period.value <= SPREAD_OPERAND_MAX
    v3 = sample_clause(np.random.default_rng(0), Distance, 3)
    assert isinstance(v3, Distance) and v3.scale == "atr"


@pytest.fixture
def ctx(tmp_path: Path) -> GateContext:
    lock = {
        "research": {"evolve_scope": "joint", "exit": {"tp_sl_ratio": 1.1, "stop_kinds": ["atr"]}},
        "derived": {"template_hash": template_hash("joint"), "exit_protocol": "bracket_timeout_v1",
                    "stop_period": STOP_PERIOD_TUNABLE, "max_tunables": 7, "grammar_version": 4},
    }  # fmt: skip
    return GateContext(ledger=Ledger.open(tmp_path / "l.db"), lock=lock)


@pytest.mark.parametrize("direction", ["long", "short"])
def test_v4_genomes_and_their_children_pass_gate_1a(ctx: GateContext, direction: str) -> None:
    """The nested ind.spread_stdev(ind.ema(...), ind.sma(...), n) call is whitelisted, unit-
    consistent (a price over a price) and carries no undeclared constant."""
    rng = np.random.default_rng(1)
    pool = [sample_genome(rng, V4) for _ in range(60)]
    genomes = list(pool)
    for i in range(240):
        kind = ("param", "point", "subtree", "crossover")[i % 4]
        a, b = pool[int(rng.integers(len(pool)))], pool[int(rng.integers(len(pool)))]
        try:
            genomes.append(breed(kind, a, rng, V4, other=b))  # type: ignore[arg-type]
        except OperatorFailed:
            continue
    spread = 0
    for g in genomes:
        src, params = render_genome(g, direction, 1.1)  # type: ignore[arg-type]
        spread += "ind.spread_stdev(" in src
        cand = StrategyCandidate(
            candidate_id="c", source=src, params=params, universe=("BTC/USDT",), timeframe="1h",
            timerange="x", run_id="r", campaign_id="c1", direction=direction,  # type: ignore[arg-type]
        )  # fmt: skip
        result = StaticGuardrail().check(cand, ctx)
        assert result.passed, f"{result.reason}\n{src}"
    assert spread > 30
