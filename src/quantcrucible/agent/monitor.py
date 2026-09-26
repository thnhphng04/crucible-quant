"""Monitoring and early stop for engine C (arch §3.2 IS→OOS curve, §3.1.11 metrics, P2-14).

- **Engine report** per (engine, seed): trials, strategies passing ④, trial efficiency
  (passing ④ per 100 trials), archive coverage (occupied cells of eligible entries), search
  coverage (cells of every trial), proposals per trial and the ranking's dispersion margin
  (INV-79) — the §3.1.11 comparison metrics that need no portfolio.
- **Degradation curve:** every ``CHECKPOINT_EVERY`` trials of an (engine, seed), the IS record
  holder (highest IS Sharpe among its trials that reached ④) and the median Sharpe of that same
  trial's CPCV-OOS paths are recorded as a ``DEGRADATION_CHECKPOINT`` audit event. When the IS
  line rises while the OOS line is flat or falling (``is_oos_diverging``), an ordinary search
  stops that engine; a comparison campaign only records a ``DEGRADATION_WARNING`` and runs on,
  because a stopping time that depends on the measured result biases the comparison (ADR-0028).

The monitor reads the CPCV paths — a ``private`` metric — which §3.2 allows for this purpose; it
is the only consumer, and nothing it reads reaches an engine or its ranking (INV-68). The holdout
is never involved (INV-47).
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from quantcrucible.agent.evolution.archive import load_entries, scope_args, trial_cells
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.evolution.ranking import RankContext, term_dispersion
from quantcrucible.agent.scheduler import Key
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent
from quantcrucible.validation.cpcv import (
    DEFAULT_DIVERGENCE,
    DegradationPoint,
    DivergenceRule,
    is_oos_diverging,
)
from quantcrucible.validation.gates import G3_IS, G4_PBO

CHECKPOINT_EVERY = 25  # trials of one (engine, seed) between two checkpoints
MonitorMode = Literal["stop", "warn"]  # ADR-0028: a comparison campaign only warns


def record_holder(ledger: Ledger, campaign_id: str, key: Key) -> tuple[str, float, float] | None:
    """(candidate_id, IS Sharpe, CPCV-OOS median Sharpe) of the IS record among the trials that
    reached gate ④, or None before any did."""
    g4 = ledger.latest_gate_results(campaign_id, G4_PBO)
    best: tuple[str, float, float] | None = None
    for t in ledger.trials(campaign_id, *scope_args(key)):
        paths = g4.get(t.candidate_id, (False, {}))[1].get("cpcv_path_sharpes")
        if not paths:
            continue
        if best is None or t.sharpe_is > best[1]:
            best = (t.candidate_id, t.sharpe_is, float(statistics.median(paths)))
    return best


def checkpoints(ledger: Ledger, campaign_id: str, key: Key) -> list[DegradationPoint]:
    out: list[DegradationPoint] = []
    for event, _island, detail in ledger.events_for(campaign_id, *scope_args(key)):
        if event == Event.DEGRADATION_CHECKPOINT and detail:
            out.append(
                DegradationPoint(
                    str(detail["candidate_id"]), float(detail["is_sharpe"]),
                    float(detail["oos_median"]),
                )
            )  # fmt: skip
    return out


def record_checkpoint(
    ledger: Ledger, campaign_id: str, key: Key, run_id: str
) -> DegradationPoint | None:
    holder = record_holder(ledger, campaign_id, key)
    if holder is None:
        return None
    cid, is_sharpe, oos = holder
    ledger.log_event(
        GenerationEvent(
            run_id=run_id, campaign_id=campaign_id, engine=key.engine, seed=key.seed,
            instrument=key.instrument, direction=key.direction, agent="monitor",
            model_used="none", event=Event.DEGRADATION_CHECKPOINT,
            detail={"candidate_id": cid, "is_sharpe": is_sharpe, "oos_median": oos,
                    "trials": len(ledger.trials(campaign_id, *scope_args(key)))},
        )
    )  # fmt: skip
    return DegradationPoint(cid, is_sharpe, oos)


def record_warning(ledger: Ledger, campaign_id: str, key: Key, run_id: str) -> None:
    """The divergence a warn-mode monitor saw and did not act on (ADR-0028), written once per
    unit of search so the report can weigh it after the run."""
    ledger.log_event(
        GenerationEvent(
            run_id=run_id, campaign_id=campaign_id, engine=key.engine, seed=key.seed,
            instrument=key.instrument, direction=key.direction, agent="monitor",
            model_used="none", event=Event.DEGRADATION_WARNING,
            detail={"signal": "is_oos_diverging",
                    "checkpoints": len(checkpoints(ledger, campaign_id, key))},
        )
    )  # fmt: skip


def already_warned(ledger: Ledger, campaign_id: str, key: Key) -> bool:
    return any(
        e == Event.DEGRADATION_WARNING
        for e, _island, _d in ledger.events_for(campaign_id, *scope_args(key))
    )


def warned_keys(ledger: Ledger, campaign_id: str, keys: Iterable[Key]) -> set[Key]:
    """Every unit whose divergence was recorded as a warning (ADR-0028)."""
    return {k for k in keys if already_warned(ledger, campaign_id, k)}


def already_stopped(
    ledger: Ledger, campaign_id: str, key: Key, rule: DivergenceRule = DEFAULT_DIVERGENCE
) -> bool:
    """Whether the checkpoints in the ledger already condemn this unit. The stop decision is
    derived state like everything else, so a restart honours it before proposing (without this,
    every restart of a stopped engine costs one more trial)."""
    return is_oos_diverging(checkpoints(ledger, campaign_id, key), rule)


def stopped_keys(
    ledger: Ledger,
    campaign_id: str,
    keys: Iterable[Key],
    rule: DivergenceRule = DEFAULT_DIVERGENCE,
) -> set[Key]:
    return {k for k in keys if already_stopped(ledger, campaign_id, k, rule)}


@dataclass
class EarlyStop:
    """The pipeline's monitor: checkpoint every ``every`` trials, then act on divergence.

    ``mode="stop"`` stops the diverging engine — the point of the signal in an ordinary search,
    where the trials it saves are the reward. ``mode="warn"`` records the divergence and lets the
    arm run on: a comparison campaign measures the arms at one budget, so the stopping time may
    not depend on the result being measured (ADR-0028).
    """

    ledger: Ledger
    campaign_id: str
    run_id: str
    every: int = CHECKPOINT_EVERY
    mode: MonitorMode = "stop"
    rule: DivergenceRule = DEFAULT_DIVERGENCE

    def __post_init__(self) -> None:
        self._last: dict[Key, int] = {}

    def __call__(self, key: Key, trials_in_ledger: int) -> bool:
        last = self._last.get(key, 0)
        if trials_in_ledger - last < self.every:
            return False
        self._last[key] = trials_in_ledger
        record_checkpoint(self.ledger, self.campaign_id, key, self.run_id)
        points = checkpoints(self.ledger, self.campaign_id, key)
        diverging = is_oos_diverging(points, self.rule)
        if diverging and self.mode == "warn":
            if not already_warned(self.ledger, self.campaign_id, key):
                record_warning(self.ledger, self.campaign_id, key, self.run_id)
            return False
        return diverging


def engine_report(
    ledger: Ledger,
    campaign_id: str,
    key: Key,
    fmap: FeatureMap,
    rule: DivergenceRule = DEFAULT_DIVERGENCE,
    periods_per_year: float | None = None,
) -> dict[str, Any]:
    """The §3.1.11 metrics of one unit of search.

    ``ranking_margin`` (INV-79) needs ``periods_per_year`` to rebuild the ranking's context; it
    is ``None`` without it. Like every other key here it is reported, never acted on: it reaches
    neither ``EarlyStop`` nor ``compare.decide`` (ADR-0028). It is reported for both arms —
    C-random ranks nothing, so its value is the counterfactual spread, which is exactly the
    comparison that exposed the compressed-benchmark defect.
    """
    trials = ledger.trials(campaign_id, *scope_args(key))
    g4 = ledger.latest_gate_results(campaign_id, G4_PBO)
    g3 = ledger.latest_gate_results(campaign_id, G3_IS)
    ids = {t.candidate_id for t in trials}
    passed4 = sum(1 for cid in ids if g4.get(cid, (False, {}))[0] and g3.get(cid, (False, {}))[0])
    submitted = sum(
        1 for e, _i, _d in ledger.events_for(campaign_id, *scope_args(key))
        if e == Event.CANDIDATE_SUBMITTED
    )  # fmt: skip
    entries = load_entries(ledger, campaign_id, key, fmap)
    margin: float | None = None
    if periods_per_year is not None and entries:
        stats = ledger.trial_stats()
        ctx = RankContext(max(stats.n_eff, 1), stats.var_sr or 0.0, periods_per_year)
        margin = term_dispersion(entries, ctx).margin
    return {
        "instrument": key.instrument,
        "direction": key.direction,
        "engine": key.engine,
        "seed": key.seed,
        "trials": len(trials),
        "passed_gate4": passed4,
        "trial_efficiency": 100.0 * passed4 / len(trials) if trials else 0.0,
        "archive_cells": len({e.cell for e in entries}),
        "search_cells": len(set(trial_cells(ledger, campaign_id, key, fmap).values())),
        "proposals_per_trial": submitted / len(trials) if trials else None,
        "diverging": is_oos_diverging(checkpoints(ledger, campaign_id, key), rule),
        "ranking_margin": margin,
    }
