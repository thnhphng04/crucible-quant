"""GP operators and ranking of engine C-gp (arch §3.1.11, §3.1.6 #1, D20, P2-11) — INV-66,
INV-68."""

from pathlib import Path

import numpy as np
import pytest

from quantcrucible.agent.engines.random_search import RandomSearch
from quantcrucible.agent.evolution.archive import Entry, load_entries
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.evolution.operators import (
    MutationKind,
    OperatorFailed,
    breed,
    choose_kind,
    mutate_param,
)
from quantcrucible.agent.evolution.ranking import RankContext, dsr_rank, scores
from quantcrucible.agent.grammar import GrammarConfig, render_genome
from quantcrucible.core.strategy.template import parse, template_hash
from quantcrucible.core.strategy.tunable import MAX_TUNABLES
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.gates import GateContext, StrategyCandidate, strategy_hash
from quantcrucible.validation.guardrail import StaticGuardrail
from quantcrucible.validation.run import FEATURE_MAP
from tests.agent.evolution.test_feature_map import _candidate

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


# ── ranking (INV-68) ─────────────────────────────────────────────────────────────────────────
def _entry(cid: str, tid: int, sr: float, sig: tuple[str, ...], n_params: int = 3) -> Entry:
    public = {"sharpe_is": sr * 19.1, "sr_obs": sr, "skew_is": 0.0, "kurtosis_is": 3.0,
              "n_obs": 2000.0, "spp_median_sharpe": 1.0, "plateau": 0.5}  # fmt: skip
    params = {f"p{i}": 1 for i in range(n_params)}
    return Entry(cid, tid, f"h{cid}", params, None, ("trend",), public, (0,), sig)


CTX = RankContext(n_trials=100, var_sr=0.5, periods_per_year=365.0)


def test_higher_sharpe_ranks_higher_and_dsr_is_deflated() -> None:
    lo, hi = _entry("a", 1, 0.03, ("x",)), _entry("b", 2, 0.08, ("y",))
    s = scores([lo, hi], CTX)
    assert s["b"] > s["a"]
    assert dsr_rank(hi, CTX) > dsr_rank(hi, RankContext(10_000, 0.5, 365.0))


def test_novelty_and_complexity_are_penalized() -> None:
    first = _entry("a", 1, 0.06, ("cmp(ema>sma)",))
    copy = _entry("b", 2, 0.06, ("cmp(ema>sma)",))
    fresh = _entry("c", 3, 0.06, ("brk(up)",))
    s = scores([first, copy, fresh], CTX)
    assert s["b"] < s["a"] and s["b"] < s["c"]
    lean, heavy = _entry("d", 1, 0.06, ("q",), 1), _entry("e", 2, 0.06, ("r",), 6)
    s2 = scores([lean, heavy], CTX)
    assert s2["d"] > s2["e"]


def test_ranking_ignores_private(tmp_path: Path) -> None:
    """INV-68: gate ④'s private detail (PBO, CPCV paths) never changes an entry or its score."""
    fm = FeatureMap.from_lock({"derived": {"feature_map": FEATURE_MAP}})
    runs = []
    for private in ({"pbo": 0.1, "cpcv_path_sharpes": [1.0]},
                    {"pbo": 0.4, "cpcv_path_sharpes": [-3.0]}):  # fmt: skip
        lg = Ledger.open(tmp_path / f"{private['pbo']}.db")
        lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
        _candidate(lg, "a", "gp", 0, 1.0, ["trend"], g4_detail=private)
        [entry] = load_entries(lg, "c1", "gp", 0, fm)
        assert "pbo" not in entry.public and "cpcv_path_sharpes" not in entry.public
        runs.append(scores([entry], CTX))
    assert runs[0] == runs[1]
