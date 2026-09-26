"""Engine C-random and the typed grammar (arch §3.1.11, P2-05) — INV-65."""

import ast
import inspect
from collections import Counter
from pathlib import Path

import pytest

from quantcrucible.agent import grammar
from quantcrucible.agent.engines import random_search
from quantcrucible.agent.engines.random_search import RandomSearch
from quantcrucible.core.strategy.template import parse, template_hash
from quantcrucible.core.strategy.tunable import MAX_TUNABLES
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.gates import GateContext, StrategyCandidate
from quantcrucible.validation.guardrail import StaticGuardrail


@pytest.fixture
def ctx(tmp_path: Path) -> GateContext:
    ledger = Ledger.open(tmp_path / "ledger.db")
    lock = {
        "research": {"evolve_scope": "joint"},
        "derived": {"template_hash": template_hash("joint")},
    }
    return GateContext(ledger=ledger, lock=lock)


def _static(ctx: GateContext, source: str, params: dict[str, float | int]) -> tuple[bool, str]:
    c = StrategyCandidate(
        candidate_id="c", source=source, params=params, universe=("BTC/USDT",), timeframe="1d",
        timerange="x", run_id="r", campaign_id="c1",
    )  # fmt: skip
    result = StaticGuardrail().check(c, ctx)
    return result.passed, result.reason


def test_a_thousand_samples_pass_gate_1a(ctx: GateContext) -> None:
    """By construction: typed, scale-invariant, ≤ 6 TUNABLE, template intact (property test)."""
    engine = RandomSearch(seed=7)
    for i in range(1000):
        p = engine.next()
        ok, reason = _static(ctx, p.source, p.params)
        assert ok, f"sample {i}: {reason}\n{p.source}"
        tunables = parse(p.source).tunables
        assert len(tunables) <= MAX_TUNABLES
        assert {t.name: t.value for t in tunables} == p.params


def test_same_seed_same_sequence() -> None:
    a, b, c = RandomSearch(seed=3), RandomSearch(seed=3), RandomSearch(seed=4)
    seq_a = [a.next().source for _ in range(50)]
    assert seq_a == [b.next().source for _ in range(50)]
    assert seq_a != [c.next().source for _ in range(50)]


def test_never_proposes_the_same_candidate_twice() -> None:
    engine = RandomSearch(seed=11)
    keys = [
        (p.source, tuple(sorted(p.params.items()))) for p in (engine.next() for _ in range(500))
    ]
    assert len(set(keys)) == len(keys)


def test_every_clause_type_and_combination_is_produced() -> None:
    engine = RandomSearch(seed=5)
    kinds: Counter[str] = Counter()
    combos: Counter[str] = Counter()
    take_profit = 0
    for _ in range(600):
        g = engine.next().genome
        kinds.update(type(c).__name__ for c in g.clauses())
        combos[g.entry.op if isinstance(g.entry, grammar.Combine) else "single"] += 1
        take_profit += g.take_profit is not None
    assert set(kinds) == {k.__name__ for k in grammar.CLAUSE_TYPES}
    assert set(combos) == {"single", "and", "or"}
    assert take_profit == 0  # O19: execution ignores take_profit, so the grammar never emits one


def test_take_profit_renders_when_enabled() -> None:
    engine = RandomSearch(seed=5, config=grammar.GrammarConfig(take_profit_probability=1.0))
    p = engine.next()
    assert "k_tp" in p.params and "self.p.k_tp * atr" in p.source


def test_the_readiness_guard_is_always_emitted() -> None:
    """ATR and the stop must be finite and positive before any entry (rsi_momentum v1 bug)."""
    engine = RandomSearch(seed=2)
    for _ in range(100):
        src = engine.next().source
        assert "ready = atr > 0 and atr - atr == 0 and stop > 0 and stop - stop == 0" in src
        assert "if ready and (" in src


def test_random_engine_has_no_result_input() -> None:
    """C-random reads no results (INV-65): it is built from a seed and a grammar config only,
    its `next()` takes nothing, and its module imports neither the ledger nor evaluation code."""
    init = inspect.signature(RandomSearch.__init__).parameters
    assert list(init) == ["self", "seed", "config"]
    assert list(inspect.signature(RandomSearch.next).parameters) == ["self"]
    for module in (random_search, grammar):
        tree = ast.parse(inspect.getsource(module))
        imported = {
            n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module
        } | {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        assert not any(m.startswith(("quantcrucible.ledger", "quantcrucible.validation"))
                       for m in imported), imported  # fmt: skip


def test_shared_param_is_one_tunable() -> None:
    """GP may share one parameter object between two nodes: it renders as one TUNABLE."""
    n = grammar.Param("period", 2, 300, 20)
    g = grammar.Genome(
        grammar.Combine("and", (
            grammar.Slope(grammar.Indicator("ema", n), True),
            grammar.Compare(grammar.Close(), ">", grammar.Indicator("ema", n)),
        )),
        grammar.Param("mult", 0.5, 5.0, 2.0),
    )  # fmt: skip
    source, params = grammar.render_genome(g)
    assert params == {"n1": 20, "k_stop": 2.0}
    assert source.count("ind.ema(bars.close, self.p.n1)") == 1
