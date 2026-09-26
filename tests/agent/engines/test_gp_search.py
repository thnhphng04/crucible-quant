"""Engine C-gp's parameter-only cap (D20) — INV-75.

`choose_kind` caps the *drawn* operator, but `point`, `subtree` and `crossover` can all render
to the parent's own code (swapping an indicator for the same one at another period, which the
template carries as a TUNABLE). The cap has to hold on what the child renders to, so it is
checked after rendering and the child is dropped when there is no room left.
"""

from pathlib import Path

import pytest

from quantcrucible.agent.engines import gp_search
from quantcrucible.agent.engines.gp_search import GpSearch
from quantcrucible.agent.engines.random_search import Proposal, RandomSearch
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.evolution.operators import changed_params_only, mutate_param
from quantcrucible.agent.grammar import categories, genome_to_dict, signature
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GateResultRecord, GenerationEvent, TrialRecord
from quantcrucible.validation.gates import G3_IS, G4_PBO, strategy_hash
from quantcrucible.validation.run import FEATURE_MAP
from tests.factories import unit

FM = FeatureMap.from_lock({"derived": {"feature_map": FEATURE_MAP}})
CAP = 0.30


def _seed(lg: Ledger, cid: str, proposal: Proposal, island: str, sharpe: float) -> None:
    """One eligible, genome-carrying entry on ``island`` — a parent for the engine to breed."""
    s_hash = strategy_hash(proposal.source)
    lg.log_event(GenerationEvent(
        run_id="r", campaign_id="c1", engine="gp", seed=0, agent="engine", model_used="none",
        event=Event.CANDIDATE_SUBMITTED, strategy_hash=s_hash,
        detail={"candidate_id": cid, "descriptors": {
            "categories": list(categories(proposal.genome)),
            "signature": list(signature(proposal.genome)),
            "genome": genome_to_dict(proposal.genome)}},
    ))  # fmt: skip
    tid = lg.record_trial(TrialRecord(
        run_id="r", campaign_id="c1", candidate_id=cid, engine="gp", seed=0,
        strategy_hash=s_hash, params=dict(proposal.params), universe="BTC/USDT", timeframe="1d",
        timerange="2018-01-01/2024-01-01", source="evolution", sharpe_is=sharpe,
        returns_path="x", verdict="PASS", island=island,
    ))  # fmt: skip
    lg.record_gate_result(GateResultRecord(
        campaign_id="c1", candidate_id=cid, gate=G3_IS, passed=True, reason="r", trial_id=tid,
        detail={"public": {"sharpe_is": sharpe, "sortino_is": sharpe * 1.3, "max_drawdown": 0.3,
                           "total_return": 0.8, "n_trades": 60.0}},
    ))  # fmt: skip
    lg.record_gate_result(GateResultRecord(
        campaign_id="c1", candidate_id=cid, gate=G4_PBO, passed=True, reason="r", trial_id=tid,
        detail={"pbo": 0.2, "cpcv_path_sharpes": [1.0]},
    ))  # fmt: skip


@pytest.fixture
def engine(tmp_path: Path) -> GpSearch:
    lg = Ledger.open(tmp_path / "l.db")
    lg.open_campaign("c1", "2024-01-01/2025-01-01", lock_hash="h")
    source = RandomSearch(seed=4)
    for k in range(4):
        _seed(lg, f"p{k}", source.next(), f"i{k}", 1.0 + 0.1 * k)
    return GpSearch(lg, "c1", unit(), 7, FM, 365.0, CAP, "run")


def test_the_cap_holds_when_every_operator_only_moves_numbers(
    engine: GpSearch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """INV-75: with every operator degenerating to a parameter move, no child is admissible at
    all — a parameter-only child needs structural siblings to stay under the cap — and the
    engine falls back to fresh genomes instead of stalling or breaking the cap."""
    monkeypatch.setattr(
        gp_search, "breed",
        lambda kind, genome, rng, config, other=None: mutate_param(genome, rng),
    )  # fmt: skip
    proposals = []
    before = engine.proposals  # the fixture's four entries are proposals of an earlier run
    for _ in range(80):
        proposals.append(engine.next())
        assert engine.param_only <= CAP * engine.children + 1e-9
    assert (engine.children, engine.param_only) == (0, 0)
    assert all(p.mutation is None and p.parents == () for p in proposals)
    assert engine.proposals - before == 80  # blocked children never stall the engine


def test_the_cap_holds_over_a_normal_run(engine: GpSearch) -> None:
    """The same invariant with the real operators, counted from the proposals themselves."""
    param_only = children = 0
    for _ in range(120):
        p = engine.next()
        if p.mutation is not None:
            children += 1
            param_only += changed_params_only(p.source, p.parents)
        assert param_only <= CAP * children + 1e-9
    assert (children, param_only) == (engine.children, engine.param_only)
    assert children > 50, "the operators bred almost nothing: the cap was not exercised"
