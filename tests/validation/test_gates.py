"""Gate pipeline (Architecture §3.2, ADR-0002) — INV-37."""

from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

import pytest

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation.gates import (
    G1A_STATIC,
    G1B_DYNAMIC,
    G2_MINBTL,
    G3_IS,
    G4_PBO,
    GateContext,
    GatePipeline,
    GateResult,
    StrategyCandidate,
    TrialMeasurement,
    strategy_hash,
)


@dataclass
class FakeGate:
    id: str
    behaviour: Callable[[], GateResult]
    cost: int = 1
    calls: int = 0

    def check(self, candidate: StrategyCandidate, ctx: GateContext) -> GateResult:
        self.calls += 1
        return self.behaviour()


def passing(gate: str, measure: bool = False) -> FakeGate:
    m = TrialMeasurement(0.7, "results/r.parquet") if measure else None
    return FakeGate(gate, lambda: GateResult(True, gate, 1.0, "ok", measurement=m))


def failing(gate: str, measure: bool = False) -> FakeGate:
    m = TrialMeasurement(-0.2, "results/r.parquet") if measure else None
    return FakeGate(gate, lambda: GateResult(False, gate, 0.0, "nope", measurement=m))


def raising(gate: str) -> FakeGate:
    def boom() -> GateResult:
        raise ZeroDivisionError("bad math")

    return FakeGate(gate, boom)


@pytest.fixture
def ctx(tmp_path: Path) -> GateContext:
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2025/2026", lock_hash="h")
    return GateContext(ledger=ledger, lock={})


def candidate(cid: str = "cand-1") -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=cid, source="x = 1\n", params={"n": 3}, universe=("BTC/USDT",),
        timeframe="1d", timerange="2018-01-01/2025-09-21", run_id="r1", campaign_id="c1",
    )  # fmt: skip


def test_gates_must_follow_arch_order() -> None:
    with pytest.raises(ValueError, match="order"):
        GatePipeline([passing(G2_MINBTL), passing(G1A_STATIC)])
    with pytest.raises(ValueError, match="unknown"):
        GatePipeline([passing("g9_magic")])


def test_passes_are_logged(ctx: GateContext) -> None:
    gates = [passing(G1A_STATIC), passing(G1B_DYNAMIC), passing(G3_IS, measure=True)]
    outcome = GatePipeline(gates).run(candidate(), ctx)
    assert outcome.passed and outcome.trial_id is not None
    assert ctx.ledger.gate_results("cand-1") == [
        (G1A_STATIC, True, "ok"),
        (G1B_DYNAMIC, True, "ok"),
        (G3_IS, True, "ok"),
    ]
    assert ctx.ledger.trial_verdicts("c1") == [(outcome.trial_id, "cand-1", "PASS")]


def test_stops_at_first_failure_and_logs_reject_event(ctx: GateContext) -> None:
    later = passing(G3_IS, measure=True)
    outcome = GatePipeline([passing(G1A_STATIC), failing(G1B_DYNAMIC), later]).run(candidate(), ctx)
    assert not outcome.passed and outcome.failed_gate == G1B_DYNAMIC
    assert later.calls == 0
    assert outcome.trial_id is None  # never measured ⇒ not a trial
    assert ctx.ledger.trial_stats().n_raw == 0
    assert (Event.LEAK_REJECT.value, strategy_hash("x = 1\n")) in ctx.ledger.events("c1")


def test_exception_rejects_and_logs(ctx: GateContext) -> None:
    outcome = GatePipeline([raising(G1A_STATIC), passing(G2_MINBTL)]).run(candidate(), ctx)
    assert not outcome.passed
    assert "ZeroDivisionError" in outcome.results[0].reason
    assert ctx.ledger.gate_results("cand-1")[0][:2] == (G1A_STATIC, False)
    assert Event.GATE_ERROR.value in [e for e, _ in ctx.ledger.events("c1")]


def test_measured_failure_is_still_a_trial(ctx: GateContext) -> None:
    outcome = GatePipeline([passing(G1A_STATIC), failing(G3_IS, measure=True)]).run(
        candidate(), ctx
    )
    assert not outcome.passed and outcome.trial_id is not None
    assert ctx.ledger.trial_verdicts("c1") == [(outcome.trial_id, "cand-1", f"REJECT_{G3_IS}")]
    assert ctx.ledger.trial_stats().n_raw == 1


def test_later_gate_results_reference_the_trial(ctx: GateContext) -> None:
    gates = [passing(G3_IS, measure=True), failing(G4_PBO)]
    outcome = GatePipeline(gates).run(candidate(), ctx)
    assert outcome.failed_gate == G4_PBO and outcome.trial_id is not None
    assert ctx.ledger.trial_verdicts("c1")[0][2] == "PASS"  # ③'s verdict; ④ is in gate_results
    assert ctx.ledger.gate_results("cand-1")[-1] == (G4_PBO, False, "nope")


def test_mislabelled_result_rejected(ctx: GateContext) -> None:
    liar = FakeGate(G1A_STATIC, lambda: GateResult(True, G3_IS, None, "ok"))
    outcome = GatePipeline([liar]).run(candidate(), ctx)
    assert not outcome.passed


def test_strategy_hash_ignores_formatting() -> None:
    assert strategy_hash("x = 1  # comment\n") == strategy_hash("x=1\n")
    assert strategy_hash("x = 1\n") != strategy_hash("x = 2\n")


def test_provenance_reaches_both_records(ctx: GateContext) -> None:
    """Engine, seed, island, parents and mutation type follow a candidate into the audit log
    and, once measured, into its trial (P2-03)."""
    c = replace(
        candidate("gp-1"), engine="gp", seed=2, island="i1", cell_id="cell-7",
        parents=("h-parent",), mutation="subtree", trial_source="evolution", agent="engine",
    )  # fmt: skip
    outcome = GatePipeline([passing(G3_IS, measure=True)]).run(c, ctx)
    assert outcome.passed
    [trial] = ctx.ledger.trials("c1", engine="gp", seed=2)
    assert (trial.island, trial.cell_id, trial.source) == ("i1", "cell-7", "evolution")
    [(event, island, detail)] = ctx.ledger.events_for("c1", engine="gp", seed=2)
    assert (event, island) == (Event.CANDIDATE_SUBMITTED, "i1")
    assert detail is not None
    assert (detail["parents"], detail["mutation"]) == (["h-parent"], "subtree")
