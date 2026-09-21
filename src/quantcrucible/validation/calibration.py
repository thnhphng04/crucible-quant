"""Calibration — §3.2.1 step 5b (Architecture §3.3.1 "optimizer off in the loop", §4.1, ADR-0017).

Runs **once**, before the freeze, for each strategy selected into the portfolio: Optuna (TPE,
seeded) searches the strategy's TUNABLE bounds with a fixed budget. Every evaluation goes
through :class:`GatePipeline` straight to gate ③ — so each one is a ``trials`` row with
``source='param_opt'`` whatever its result (no hidden trials, MadEvolve X2), and each raises
``N``. The best passing parameters are then re-submitted under the **same candidate id**
through the full candidate pipeline ①a → ④: PBO is recomputed (the calibration rows are now
ledger variants of the configuration set) and, if it passes, this trial replaces the original
in the next portfolio build, whose gate ⑤ recomputes DSR.

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
from quantcrucible.validation.gates import (
    GateContext,
    GatePipeline,
    PipelineOutcome,
    StrategyCandidate,
)
from quantcrucible.validation.is_gates import InSampleGate

FAILED_SCORE = -10.0  # objective for an evaluation rejected at ③ (it is still a trial)


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    candidate_id: str
    evaluations: int
    best_params: dict[str, float | int]
    best_sharpe: float | None  # None: no evaluation passed ③
    confirmation: PipelineOutcome | None  # the full ①a → ④ re-run of the best parameters


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
        if outcome.passed and result.value is not None:
            scored.append((float(result.value), params))
            return float(result.value)
        return FAILED_SCORE

    logging.getLogger("optuna").setLevel(logging.WARNING)
    study = optuna.create_study(direction="maximize", sampler=optuna.samplers.TPESampler(seed=seed))
    study.optimize(objective, n_trials=budget)
    if not scored:
        return CalibrationResult(candidate.candidate_id, budget, dict(candidate.params), None, None)
    best_sharpe, best = max(scored, key=lambda s: s[0])
    confirmed = dataclasses.replace(
        candidate, params=best, run_id=f"calib-{candidate.candidate_id}", trial_source="param_opt"
    )
    outcome = full_pipeline.run(confirmed, ctx)
    return CalibrationResult(candidate.candidate_id, budget, best, best_sharpe, outcome)


def calibration_settings(lock: Mapping[str, object]) -> tuple[bool, int]:
    research = lock["research"]
    assert isinstance(research, Mapping)
    cal = research["calibration"]
    assert isinstance(cal, Mapping)
    return bool(cal["enabled"]), int(cal["budget_per_strategy"])
