"""GP operators of engine C-gp (arch §3.1.11, D20, P2-11) — INV-66, INV-75.

The ranking score has its own mirror, tests/agent/evolution/test_ranking.py."""

from pathlib import Path

import numpy as np
import pytest

from quantcrucible.agent.engines.random_search import RandomSearch
from quantcrucible.agent.evolution.operators import (
    MutationKind,
    OperatorFailed,
    breed,
    changed_params_only,
    choose_kind,
    mutate_param,
)
from quantcrucible.agent.grammar import GrammarConfig, render_genome
from quantcrucible.core.strategy.template import parse, template_hash
from quantcrucible.core.strategy.tunable import MAX_TUNABLES
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.gates import GateContext, StrategyCandidate, strategy_hash
from quantcrucible.validation.guardrail import StaticGuardrail

CFG = GrammarConfig()


@pytest.fixture
def ctx(tmp_path: Path) -> GateContext:
    lock = {"research": {"evolve_scope": "joint"},
            "derived": {"template_hash": template_hash("joint")}}  # fmt: skip
    return GateContext(ledger=Ledger.open(tmp_path / "l.db"), lock=lock)


def _passes_1a(ctx: GateContext, source: str, params: dict[str, float | int]) -> tuple[bool, str]:
    c = StrategyCandidate(
        candidate_id="c", source=source, params=params, universe=("BTC/USDT",), timeframe="1d",
        timerange="x", run_id="r", campaign_id="c1",
    )  # fmt: skip
    r = StaticGuardrail().check(c, ctx)
    return r.passed, r.reason


def test_every_child_is_typed_and_passes_gate_1a(ctx: GateContext) -> None:
    """Property test: 600 children from every operator stay inside the grammar."""
    rng = np.random.default_rng(1)
    pool = [p.genome for p in (RandomSearch(seed=9).next() for _ in range(40))]
    made = 0
    for i in range(600):
        kind: MutationKind = ("param", "point", "subtree", "crossover")[i % 4]
        a, b = pool[int(rng.integers(len(pool)))], pool[int(rng.integers(len(pool)))]
        try:
            child = breed(kind, a, rng, CFG, other=b)
        except OperatorFailed:
            continue
        source, params = render_genome(child)
        ok, reason = _passes_1a(ctx, source, params)
        assert ok, f"{kind}: {reason}\n{source}"
        assert len(parse(source).tunables) <= MAX_TUNABLES
        made += 1
    assert made > 500


def test_a_parameter_only_child_keeps_the_parent_hash() -> None:
    rng = np.random.default_rng(4)
    for p in (RandomSearch(seed=21).next() for _ in range(50)):
        child = mutate_param(p.genome, rng)
        src, params = render_genome(child)
        assert strategy_hash(src) == strategy_hash(p.source)
        assert params != p.params


def test_structural_children_change_the_code() -> None:
    rng = np.random.default_rng(6)
    parents = [p.genome for p in (RandomSearch(seed=3).next() for _ in range(30))]
    changed = 0
    for g in parents:
        for kind in ("point", "subtree"):
            try:
                child = breed(kind, g, rng, CFG)
            except OperatorFailed:
                continue
            changed += strategy_hash(render_genome(child)[0]) != strategy_hash(render_genome(g)[0])
    assert changed >= 50


def test_a_structural_child_that_only_moved_numbers_counts_as_parameter_only() -> None:
    """INV-66 is about what changed, not about the label: `point` and `subtree` can swap an
    indicator for the same indicator at another period, which the template carries as a TUNABLE.
    Counting the label lets such children past `param_only_max`."""
    rng = np.random.default_rng(11)
    labelled_structural = 0
    for p in (RandomSearch(seed=5).next() for _ in range(60)):
        parents = (strategy_hash(p.source),)
        assert changed_params_only(render_genome(mutate_param(p.genome, rng))[0], parents)
        assert not changed_params_only(render_genome(p.genome)[0], ())  # a fresh genome has none
        for kind in ("point", "subtree"):
            try:
                child = breed(kind, p.genome, rng, CFG)
            except OperatorFailed:
                continue
            labelled_structural += changed_params_only(render_genome(child)[0], parents)
    assert labelled_structural > 0, "no structural child kept its parent's code: widen the search"


def test_param_only_cap() -> None:
    """INV-66: whatever the draw, the parameter-only share of the offspring never exceeds
    `param_only_max`."""
    rng = np.random.default_rng(0)
    for cap in (0.0, 0.1, 0.3, 1.0):
        children = param_only = 0
        for _ in range(2000):
            kind = choose_kind(rng, children, param_only, cap, p_param=0.9)
            param_only += kind == "param"
            children += 1
            assert param_only <= cap * children + 1e-9
        if cap >= 0.3:
            assert param_only / children == pytest.approx(min(cap, 0.9), abs=0.02)


def test_crossover_needs_a_second_parent() -> None:
    g = RandomSearch(seed=1).next().genome
    with pytest.raises(OperatorFailed):
        breed("crossover", g, np.random.default_rng(0), CFG)
