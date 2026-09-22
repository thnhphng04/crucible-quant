"""Calibration — §3.2.1 step 5b (Architecture §3.3.1 "optimizer off in the loop", §4.1, ADR-0017).

Runs **once**, before the freeze, for each strategy selected into the portfolio: Optuna (TPE,
seeded) searches the strategy's TUNABLE bounds with a fixed budget. The ledger enforces it
(``calibration_runs``): a second run is refused; only a run that measured nothing is retried.
Every evaluation goes through :class:`GatePipeline` straight to gate ③. An attempt whose returns
were measured is a ``trials`` row with ``source='param_opt'`` whatever its verdict (no hidden
trials, MadEvolve X2), and raises ``N``; an attempt that failed before anything was measured
(e.g. the strategy raised in the sandbox) has no Sharpe and no returns, so it cannot be a trial
(§4.1) — it is recorded as an error, never given a made-up Sharpe. Every attempt, measured or
not, is listed in one ``CALIBRATION_FINISHED`` audit event (ADR-0017 amendment).

The best passing parameters are then re-submitted under the **same candidate id** through the
full candidate pipeline ①a → ④: PBO is recomputed (the calibration rows are now ledger variants
of the configuration set) and, if it passes, this trial replaces the original in the next
portfolio build, whose gate ⑤ recomputes DSR.

This is the only module that may import an optimizer (``tests/test_architecture_boundaries.py``).
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Mapping
from dataclasses import dataclass

import optuna

from quantcrucible.core.strategy.template import parse
from quantcrucible.core.strategy.tunable import Tunable
from quantcrucible.ledger.records import Event, GenerationEvent
from quantcrucible.validation.gates import (
    G4_PBO,
    GateContext,
    GatePipeline,
    PipelineOutcome,
    StrategyCandidate,
)
from quantcrucible.validation.is_gates import InSampleGate

FAILED_SCORE = -10.0  # Optuna's objective for a rejected or failed attempt — search only, never
# stored anywhere as a Sharpe


class CalibrationError(RuntimeError):
    """Calibration stopped: an attempt needs a human to classify it (ADR-0022)."""


class CalibrationRefused(CalibrationError):
    """Calibration may not run: it runs once per strategy per campaign (ADR-0017 amendment 2)."""


@dataclass(frozen=True, slots=True)
class Attempt:
    """One Optuna attempt (ADR-0022 counting convention).

    ``passed`` / ``rejected``: measured at ③ — a trials row, counted in N and V[SR].
    ``error``: failed before any performance output — no trial, not in N or V[SR], traced here.
    ``error_after_output``: a performance number was produced but nothing was measured — never
    exempted automatically; calibration stops so a human can classify it."""

    attempt_id: str  # <candidate_id>-optNNN — also the candidate id in gate_results
    params: dict[str, float | int]
    outcome: str  # passed | rejected | error | error_after_output
    trial_id: int | None
    sharpe_is: float | None
    reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "attempt_id": self.attempt_id, "params": self.params, "outcome": self.outcome,
            "trial_id": self.trial_id, "sharpe_is": self.sharpe_is, "reason": self.reason,
        }  # fmt: skip


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    candidate_id: str
    attempts: tuple[Attempt, ...]
    best_params: dict[str, float | int]
    best_sharpe: float | None  # None: no attempt passed ③
    confirmation: PipelineOutcome | None  # the full ①a → ④ re-run of the best parameters

    @property
    def evaluations(self) -> int:
        return len(self.attempts)

    @property
    def n_trials(self) -> int:
        return sum(not a.outcome.startswith("error") for a in self.attempts)

    @property
    def n_errors(self) -> int:
        return sum(a.outcome.startswith("error") for a in self.attempts)


def _suggest(trial: optuna.Trial, t: Tunable) -> float | int:
    if t.is_int:
        return trial.suggest_int(t.name, int(t.low), int(t.high))
    return trial.suggest_float(t.name, t.low, t.high)


def calibrate(
    candidate: StrategyCandidate,
    ctx: GateContext,
    budget: int,
    full_pipeline: GatePipeline,
    seed: int = 0,
    measure: GatePipeline | None = None,
) -> CalibrationResult:
    """Search, record every evaluation, then confirm the best through ``full_pipeline``."""
    if budget < 1:
        raise ValueError("budget must be >= 1")
    tunables = list(parse(candidate.source).tunables)
    if not tunables:
        raise ValueError("nothing to calibrate: the strategy declares no TUNABLE")
    measure = measure or GatePipeline([InSampleGate()])
    retry = _register(ctx, candidate, budget)
    _log(ctx, candidate, Event.CALIBRATION_STARTED, {"budget": budget, "technical_retry": retry})
    scored: list[tuple[float, dict[str, float | int]]] = []
    attempts: list[Attempt] = []

    def objective(trial: optuna.Trial) -> float:
        params = {t.name: _suggest(trial, t) for t in tunables}
        evaluation = dataclasses.replace(
            candidate,
            candidate_id=f"{candidate.candidate_id}-opt{trial.number:03d}",
            params=params,
            run_id=f"calib-{candidate.candidate_id}",
            trial_source="param_opt",
        )
        outcome = measure.run(evaluation, ctx)
        result = outcome.results[-1]
        measured = outcome.trial_id is not None
        output = result.value is not None or result.report is not None
        kind = (
            ("passed" if outcome.passed else "rejected") if measured
            else "error_after_output" if output else "error"
        )  # fmt: skip
        attempts.append(
            Attempt(
                attempt_id=evaluation.candidate_id, params=params, outcome=kind,
                trial_id=outcome.trial_id,
                sharpe_is=float(result.value) if measured and result.value is not None else None,
                reason=None if outcome.passed else result.reason,
            )
        )  # fmt: skip
        if kind == "error_after_output":
            trial.study.stop()  # no further attempt until a human classifies this one
            return FAILED_SCORE
        if outcome.passed and result.value is not None:
            scored.append((float(result.value), params))
            return float(result.value)
        return FAILED_SCORE

    logging.getLogger("optuna").setLevel(logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=budget)
    _log_attempts(ctx, candidate, attempts)
    unclassified = [a.attempt_id for a in attempts if a.outcome == "error_after_output"]
    n_errors = sum(a.outcome.startswith("error") for a in attempts)
    ctx.ledger.finish_calibration(
        candidate.campaign_id, candidate.candidate_id, "stopped" if unclassified else "finished",
        len(attempts), len(attempts) - n_errors, n_errors,
    )  # fmt: skip
    if unclassified:
        raise CalibrationError(
            f"{unclassified}: a performance number was produced without a measurement — "
            "classify it (count it in N or not, ADR-0022) before calibrating further"
        )
    if not scored:
        _log(ctx, candidate, Event.CALIBRATION_CONFIRMATION, {"status": "not_run"})
        return CalibrationResult(
            candidate.candidate_id, tuple(attempts), dict(candidate.params), None, None
        )
    best_sharpe, best = max(scored, key=lambda s: s[0])
    confirmed = dataclasses.replace(
        candidate, params=best, run_id=f"calib-{candidate.candidate_id}", trial_source="param_opt"
    )
    # The search is over and its budget spent; from here only this one parameter set is measured.
    try:
        outcome = full_pipeline.run(confirmed, ctx)
    except Exception as e:
        _log(ctx, candidate, Event.CALIBRATION_CONFIRMATION,
             {"status": "error", "params": best, "error": f"{type(e).__name__}: {e}"})  # fmt: skip
        raise CalibrationError(
            f"{candidate.candidate_id}: budget used ({len(attempts)} of {budget} attempts); "
            f"confirmation failed ({type(e).__name__}: {e}) — calibration did NOT succeed"
        ) from e
    status = "passed" if outcome.passed else "rejected"
    _log(ctx, candidate, Event.CALIBRATION_CONFIRMATION,
         {"status": status, "params": best, "trial_id": outcome.trial_id})  # fmt: skip
    return CalibrationResult(candidate.candidate_id, tuple(attempts), best, best_sharpe, outcome)


def confirmation_status(ctx: GateContext, campaign_id: str, candidate_id: str) -> str:
    """``passed`` / ``rejected`` / ``not_run`` (no attempt passed ③) / ``error`` /
    ``incomplete`` (no record: the process died during the confirmation)."""
    events = [
        (e, d) for e, d in ctx.ledger.event_details(campaign_id)
        if d is not None and d.get("candidate_id") == candidate_id
    ]  # fmt: skip
    for event, detail in reversed(events):
        if event == Event.CALIBRATION_CONFIRMATION:
            return str(detail["status"])
    if any(event == Event.CALIBRATION_STARTED for event, _ in events):
        return "incomplete"  # a confirmation that measured at ③ but never finished ④ included
    # runs older than these events: the confirmation is the candidate's own `param_opt` trial
    confirmations = [
        t for t in ctx.ledger.trials(campaign_id)
        if t.candidate_id == candidate_id and t.source == "param_opt"
    ]  # fmt: skip
    if confirmations:
        passed = ctx.ledger.passed_trials(campaign_id, G4_PBO)
        return "passed" if confirmations[-1].id in passed else "rejected"
    return "incomplete"


def _register(ctx: GateContext, c: StrategyCandidate, budget: int) -> bool:
    retry = check_allowed(ctx, c, budget)
    if not retry:
        ctx.ledger.start_calibration(c.campaign_id, c.candidate_id, c.strategy_hash, budget)
    return retry


def check_allowed(ctx: GateContext, c: StrategyCandidate, budget: int) -> bool:
    """Once per strategy per campaign, with a fixed budget (§3.2.1 5b). Returns True for a
    technical retry: a registered run that never finished and recorded no attempt result —
    nothing was measured, so running it again searches no further. Anything else is refused:
    more search after seeing results would spend budget the rule does not allow."""
    prior = ctx.ledger.calibration_run(c.campaign_id, c.candidate_id, c.strategy_hash)
    if prior is None:
        return False
    where = f"{prior.candidate_id!r} (strategy {prior.strategy_hash[:12]}…) in {c.campaign_id}"
    if prior.outcome == "stopped":
        raise CalibrationRefused(
            f"{where} was already calibrated and stopped after {prior.attempts} of "
            f"{prior.budget} attempts (an attempt awaits classification, ADR-0022): "
            "calibration runs once per strategy per campaign"
        )
    if prior.outcome is not None:
        status = confirmation_status(ctx, c.campaign_id, prior.candidate_id)
        verdict = {
            "passed": "confirmation passed",
            "rejected": "confirmation rejected",
            "not_run": "no attempt passed ③, nothing to confirm",
        }.get(status, "confirmation failed or incomplete — calibration did NOT succeed")
        raise CalibrationRefused(
            f"{where} was already calibrated: search finished, budget used ({prior.attempts} "
            f"of {prior.budget} attempts); {verdict}. Optuna never runs again for it; "
            "re-running the confirmation is not supported"
        )
    if (prior.candidate_id, prior.strategy_hash, prior.budget) != (
        c.candidate_id,
        c.strategy_hash,
        budget,
    ):
        raise CalibrationRefused(
            f"an unfinished calibration of {where} has budget {prior.budget}: a retry must be the "
            "same run (same candidate, code and budget)"
        )
    recorded = ctx.ledger.calibration_attempts_recorded(c.campaign_id, c.candidate_id)
    if recorded:
        raise CalibrationRefused(
            f"the calibration of {where} was interrupted after {recorded} attempt result(s) were "
            "recorded: running it again would search further — a human decides"
        )
    return True


def _log(ctx: GateContext, c: StrategyCandidate, event: Event, detail: dict[str, object]) -> None:
    ctx.ledger.log_event(
        GenerationEvent(
            run_id=f"calib-{c.candidate_id}", campaign_id=c.campaign_id, engine=c.engine,
            seed=c.seed, agent=c.agent, model_used=c.model_used, event=event,
            evolve_scope=c.evolve_scope, strategy_hash=c.strategy_hash,
            detail={"candidate_id": c.candidate_id, **detail},
        )
    )  # fmt: skip


def _log_attempts(ctx: GateContext, c: StrategyCandidate, attempts: list[Attempt]) -> None:
    """One audit event that accounts for every attempt: B = trials + errors."""
    n_errors = sum(a.outcome.startswith("error") for a in attempts)
    _log(ctx, c, Event.CALIBRATION_FINISHED, {
        "attempts": len(attempts), "trials": len(attempts) - n_errors, "errors": n_errors,
        "log": [a.as_dict() for a in attempts],
    })  # fmt: skip


def calibration_settings(lock: Mapping[str, object]) -> tuple[bool, int]:
    research = lock["research"]
    assert isinstance(research, Mapping)
    cal = research["calibration"]
    assert isinstance(cal, Mapping)
    return bool(cal["enabled"]), int(cal["budget_per_strategy"])
