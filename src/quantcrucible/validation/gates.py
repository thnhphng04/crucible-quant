"""Gate protocol and pipeline (Architecture §3.2, ADR-0002).

Gates run in the explicit order of the §3.2 table (not sorted by ``cost``) and the pipeline stops
at the first failure. Every result — pass or fail — is written to ``gate_results``; rejections
are also logged as audit events. From gate ③ on, a candidate whose performance was measured on
data is a statistical trial (a ``trials`` row), whatever its verdict. Gates fail closed: an
exception inside a gate is a rejection.
"""

from __future__ import annotations

import ast
import hashlib
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import (
    Event,
    GateResultRecord,
    GenerationEvent,
    TrialRecord,
    TrialSource,
)
from quantcrucible.validation.report import EvaluationReport

G0_DRIFT_STATIC = "g0_drift_static"
G1A_STATIC = "g1a_static"
G0_DRIFT_TRACE = "g0_drift_trace"
G1B_DYNAMIC = "g1b_dynamic"
G2_MINBTL = "g2_minbtl"
G3_IS = "g3_is"
G4_PBO = "g4_pbo"

GATE_ORDER: tuple[str, ...] = (
    G0_DRIFT_STATIC,
    G1A_STATIC,
    G0_DRIFT_TRACE,
    G1B_DYNAMIC,
    G2_MINBTL,
    G3_IS,
    G4_PBO,
)
TRIAL_GATE = G3_IS

REJECT_EVENTS: dict[str, Event] = {
    G0_DRIFT_STATIC: Event.DRIFT_REJECT,
    G1A_STATIC: Event.AST_REJECT,
    G0_DRIFT_TRACE: Event.DRIFT_REJECT,
    G1B_DYNAMIC: Event.LEAK_REJECT,
    G2_MINBTL: Event.MINBTL_REJECT,
}


def strategy_hash(source: str) -> str:
    """SHA256 of the normalized code: comments and formatting do not change it (§4.1)."""
    try:
        normalized = ast.dump(ast.parse(source), annotate_fields=False, include_attributes=False)
    except SyntaxError:
        normalized = source
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StrategyCandidate:
    candidate_id: str
    source: str
    params: Mapping[str, float | int]
    universe: tuple[str, ...]
    timeframe: str
    timerange: str
    run_id: str
    campaign_id: str
    engine: str = "manual"
    seed: int = 0
    evolve_scope: str = "joint"
    hypothesis: str | None = None
    cell_id: str | None = None
    trial_source: TrialSource = "manual"
    agent: str = "human"
    model_used: str = "none"

    @property
    def strategy_hash(self) -> str:
        return strategy_hash(self.source)


@dataclass(frozen=True, slots=True)
class TrialMeasurement:
    """What a trial-stage gate measured on data — makes the candidate a statistical trial."""

    sharpe_is: float
    returns_path: str


@dataclass(frozen=True, slots=True)
class GateResult:
    passed: bool
    gate: str
    value: float | None
    reason: str
    detail: Mapping[str, Any] | None = None
    event: Event | None = None  # overrides the default reject event (e.g. TEMPLATE_TAMPER)
    report: EvaluationReport | None = None
    measurement: TrialMeasurement | None = None


@dataclass(slots=True)
class GateContext:
    """What gates may use. Research settings come from the campaign lock, never user.yaml."""

    ledger: Ledger
    lock: Mapping[str, Any]
    services: dict[str, Any] = field(default_factory=dict)  # sandbox runner, IS data, …


class Gate(Protocol):
    id: str
    cost: int

    def check(self, candidate: StrategyCandidate, ctx: GateContext) -> GateResult: ...


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    candidate_id: str
    passed: bool
    results: tuple[GateResult, ...]
    trial_id: int | None

    @property
    def failed_gate(self) -> str | None:
        return next((r.gate for r in self.results if not r.passed), None)


class GatePipeline:
    def __init__(self, gates: Sequence[Gate]) -> None:
        ids = [g.id for g in gates]
        unknown = [i for i in ids if i not in GATE_ORDER]
        if unknown:
            raise ValueError(f"unknown gate id(s) {unknown}")
        if ids != sorted(ids, key=GATE_ORDER.index) or len(set(ids)) != len(ids):
            raise ValueError(f"gates must follow the §3.2 order {GATE_ORDER}, got {ids}")
        self.gates = tuple(gates)

    def run(self, candidate: StrategyCandidate, ctx: GateContext) -> PipelineOutcome:
        ledger = ctx.ledger
        c = candidate
        s_hash = c.strategy_hash
        archive = ctx.services.get("archive")  # StrategyArchive — later stages re-run members
        if archive is not None:
            archive.put(c.source)
        ledger.log_event(self._event(c, Event.CANDIDATE_SUBMITTED, s_hash))
        results: list[GateResult] = []
        trial_id: int | None = None
        for gate in self.gates:
            result = self._check(gate, c, ctx)
            results.append(result)
            if result.measurement is not None and trial_id is None:
                if GATE_ORDER.index(gate.id) < GATE_ORDER.index(TRIAL_GATE):
                    raise RuntimeError(f"gate {gate.id} measured performance before gate ③")
                trial_id = ledger.record_trial(
                    TrialRecord(
                        run_id=c.run_id, campaign_id=c.campaign_id, candidate_id=c.candidate_id,
                        engine=c.engine, seed=c.seed, strategy_hash=s_hash, params=dict(c.params),
                        universe=",".join(c.universe), timeframe=c.timeframe,
                        timerange=c.timerange, source=c.trial_source,
                        sharpe_is=result.measurement.sharpe_is,
                        returns_path=result.measurement.returns_path,
                        verdict="PASS" if result.passed else f"REJECT_{gate.id}",
                        evolve_scope=c.evolve_scope, hypothesis=c.hypothesis, cell_id=c.cell_id,
                        gate_failed=None if result.passed else gate.id,
                    )
                )  # fmt: skip
            ledger.record_gate_result(
                GateResultRecord(
                    campaign_id=c.campaign_id, candidate_id=c.candidate_id, gate=gate.id,
                    passed=result.passed, reason=result.reason, value=result.value,
                    strategy_hash=s_hash, trial_id=trial_id,
                    detail=dict(result.detail) if result.detail else None,
                )
            )  # fmt: skip
            if not result.passed:
                event = result.event or REJECT_EVENTS.get(gate.id, Event.GATE_REJECT)
                detail = {"gate": gate.id, "reason": result.reason, **(result.detail or {})}
                ledger.log_event(self._event(c, event, s_hash, detail))
                return PipelineOutcome(c.candidate_id, False, tuple(results), trial_id)
        return PipelineOutcome(c.candidate_id, True, tuple(results), trial_id)

    @staticmethod
    def _check(gate: Gate, candidate: StrategyCandidate, ctx: GateContext) -> GateResult:
        try:
            result = gate.check(candidate, ctx)
            if result.gate != gate.id:
                raise RuntimeError(f"gate {gate.id} returned a result labelled {result.gate}")
            return result
        except Exception as e:  # fail closed (§3.2)
            return GateResult(
                passed=False,
                gate=gate.id,
                value=None,
                reason=f"exception in gate: {type(e).__name__}: {e}",
                detail={"traceback": traceback.format_exc(limit=20)},
                event=Event.GATE_ERROR,
            )

    @staticmethod
    def _event(
        c: StrategyCandidate,
        event: Event,
        s_hash: str,
        detail: dict[str, Any] | None = None,
    ) -> GenerationEvent:
        return GenerationEvent(
            run_id=c.run_id, campaign_id=c.campaign_id, engine=c.engine, seed=c.seed,
            agent=c.agent, model_used=c.model_used, event=event, evolve_scope=c.evolve_scope,
            cell_id=c.cell_id, strategy_hash=s_hash,
            detail={"candidate_id": c.candidate_id, **(detail or {})},
        )  # fmt: skip
