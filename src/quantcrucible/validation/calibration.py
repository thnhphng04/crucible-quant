"""Calibration — §3.2.1 step 5b (Architecture §3.3.1 "optimizer off in the loop", §4.1, ADR-0017).

Runs **once**, before the freeze, for each strategy selected into the portfolio: Optuna (TPE,
seeded) searches the strategy's TUNABLE bounds with a fixed budget. Every evaluation goes
through :class:`GatePipeline` straight to gate ③. An attempt whose returns were measured is a
``trials`` row with ``source='param_opt'`` whatever its verdict (no hidden trials, MadEvolve X2),
and raises ``N``; an attempt that failed before anything was measured (e.g. the strategy raised
in the sandbox) has no Sharpe and no returns, so it cannot be a trial (§4.1) — it is recorded as an
error, never given a made-up Sharpe. Every attempt, measured or not, is listed in one
``CALIBRATION_FINISHED`` audit event (ADR-0017 amendment).

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
    GateContext,
    GatePipeline,
    PipelineOutcome,
    StrategyCandidate,
)
from quantcrucible.validation.is_gates import InSampleGate

FAILED_SCORE = -10.0  # Optuna's objective for a rejected or failed attempt — search only, never
# stored anywhere as a Sharpe


@dataclass(frozen=True, slots=True)
class Attempt:
    """One Optuna attempt. ``passed`` / ``rejected``: measured at ③, a trials row. ``error``:
    nothing measured — no trial, no Sharpe; ``reason`` says why."""

    attempt_id: str  # <candidate_id>-optNNN — also the candidate id in gate_results
    params: dict[str, float | int]
    outcome: str  # passed | rejected | error
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
        return sum(a.outcome != "error" for a in self.attempts)

    @property
    def n_errors(self) -> int:
        return sum(a.outcome == "error" for a in self.attempts)


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
) -> CalibrationResult:
    """Search, record every evaluation, then confirm the best through ``full_pipeline``."""
    if budget < 1:
        raise ValueError("budget must be >= 1")
    tunables = list(parse(candidate.source).tunables)
    if not tunables:
        raise ValueError("nothing to calibrate: the strategy declares no TUNABLE")
    measure = GatePipeline([InSampleGate()])
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
        attempts.append(
            Attempt(
                attempt_id=evaluation.candidate_id, params=params,
                outcome="error" if not measured else "passed" if outcome.passed else "rejected",
                trial_id=outcome.trial_id,
                sharpe_is=float(result.value) if measured and result.value is not None else None,
                reason=None if outcome.passed else result.reason,
            )
        )  # fmt: skip
        if outcome.passed and result.value is not None:
            scored.append((float(result.value), params))
            return float(result.value)
        return FAILED_SCORE

    logging.getLogger("optuna").setLevel(logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=budget)
    _log_attempts(ctx, candidate, attempts)
    if not scored:
        return CalibrationResult(
            candidate.candidate_id, tuple(attempts), dict(candidate.params), None, None
        )
    best_sharpe, best = max(scored, key=lambda s: s[0])
    confirmed = dataclasses.replace(
        candidate, params=best, run_id=f"calib-{candidate.candidate_id}", trial_source="param_opt"
    )
    outcome = full_pipeline.run(confirmed, ctx)
    return CalibrationResult(candidate.candidate_id, tuple(attempts), best, best_sharpe, outcome)


def _log_attempts(ctx: GateContext, c: StrategyCandidate, attempts: list[Attempt]) -> None:
    """One audit event that accounts for every attempt: B = trials + errors."""
    n_errors = sum(a.outcome == "error" for a in attempts)
    ctx.ledger.log_event(
        GenerationEvent(
            run_id=f"calib-{c.candidate_id}", campaign_id=c.campaign_id, engine=c.engine,
            seed=c.seed, agent=c.agent, model_used=c.model_used,
            event=Event.CALIBRATION_FINISHED, evolve_scope=c.evolve_scope,
            strategy_hash=c.strategy_hash,
            detail={
                "candidate_id": c.candidate_id, "attempts": len(attempts),
                "trials": len(attempts) - n_errors, "errors": n_errors,
                "log": [a.as_dict() for a in attempts],
            },
        )
    )  # fmt: skip


def calibration_settings(lock: Mapping[str, object]) -> tuple[bool, int]:
    research = lock["research"]
    assert isinstance(research, Mapping)
    cal = research["calibration"]
    assert isinstance(cal, Mapping)
    return bool(cal["enabled"]), int(cal["budget_per_strategy"])
