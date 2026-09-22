"""Monitoring and early stop for engine C (arch §3.2 IS→OOS curve, §3.1.11 metrics, P2-14).

- **Engine report** per (engine, seed): trials, strategies passing ④, trial efficiency
  (passing ④ per 100 trials), archive coverage (occupied cells of eligible entries), search
  coverage (cells of every trial) and proposals per trial — the §3.1.11 comparison metrics that
  need no portfolio.
- **Degradation curve:** every ``CHECKPOINT_EVERY`` trials of an (engine, seed), the IS record
  holder (highest IS Sharpe among its trials that reached ④) and the median Sharpe of that same
  trial's CPCV-OOS paths are recorded as a ``DEGRADATION_CHECKPOINT`` audit event. When the IS
  line rises while the OOS line is flat or falling (``is_oos_diverging``), the engine is stopped.

The monitor reads the CPCV paths — a ``private`` metric — which §3.2 allows for this purpose; it
is the only consumer, and nothing it reads reaches an engine or its ranking (INV-68). The holdout
is never involved (INV-47).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

from quantcrucible.agent.evolution.archive import load_entries, trial_cells
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.scheduler import Key
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent
from quantcrucible.validation.cpcv import DegradationPoint, is_oos_diverging
from quantcrucible.validation.gates import G3_IS, G4_PBO

CHECKPOINT_EVERY = 25  # trials of one (engine, seed) between two checkpoints


def record_holder(
    ledger: Ledger, campaign_id: str, engine: str, seed: int
) -> tuple[str, float, float] | None:
    """(candidate_id, IS Sharpe, CPCV-OOS median Sharpe) of the IS record among the trials that
    reached gate ④, or None before any did."""
    g4 = ledger.latest_gate_results(campaign_id, G4_PBO)
    best: tuple[str, float, float] | None = None
    for t in ledger.trials(campaign_id, engine=engine, seed=seed):
        paths = g4.get(t.candidate_id, (False, {}))[1].get("cpcv_path_sharpes")
        if not paths:
            continue
        if best is None or t.sharpe_is > best[1]:
            best = (t.candidate_id, t.sharpe_is, float(statistics.median(paths)))
    return best


def checkpoints(ledger: Ledger, campaign_id: str, engine: str, seed: int) -> list[DegradationPoint]:
    out: list[DegradationPoint] = []
    for event, _island, detail in ledger.events_for(campaign_id, engine=engine, seed=seed):
        if event == Event.DEGRADATION_CHECKPOINT and detail:
            out.append(
                DegradationPoint(
                    str(detail["candidate_id"]), float(detail["is_sharpe"]),
                    float(detail["oos_median"]),
                )
            )  # fmt: skip
    return out


def record_checkpoint(
    ledger: Ledger, campaign_id: str, engine: str, seed: int, run_id: str
) -> DegradationPoint | None:
    holder = record_holder(ledger, campaign_id, engine, seed)
    if holder is None:
        return None
    cid, is_sharpe, oos = holder
    ledger.log_event(
        GenerationEvent(
            run_id=run_id, campaign_id=campaign_id, engine=engine, seed=seed, agent="monitor",
            model_used="none", event=Event.DEGRADATION_CHECKPOINT,
            detail={"candidate_id": cid, "is_sharpe": is_sharpe, "oos_median": oos,
                    "trials": len(ledger.trials(campaign_id, engine=engine, seed=seed))},
        )
    )  # fmt: skip
    return DegradationPoint(cid, is_sharpe, oos)


@dataclass
class EarlyStop:
    """The pipeline's monitor: checkpoint every ``every`` trials, stop a diverging engine."""

    ledger: Ledger
    campaign_id: str
    run_id: str
    every: int = CHECKPOINT_EVERY

    def __post_init__(self) -> None:
        self._last: dict[Key, int] = {}

    def __call__(self, key: Key, trials_in_ledger: int) -> bool:
        engine, seed = key
        last = self._last.get(key, 0)
        if trials_in_ledger - last < self.every:
            return False
        self._last[key] = trials_in_ledger
        record_checkpoint(self.ledger, self.campaign_id, engine, seed, self.run_id)
        return is_oos_diverging(checkpoints(self.ledger, self.campaign_id, engine, seed))


def engine_report(
    ledger: Ledger, campaign_id: str, engine: str, seed: int, fmap: FeatureMap
) -> dict[str, Any]:
    trials = ledger.trials(campaign_id, engine=engine, seed=seed)
    g4 = ledger.latest_gate_results(campaign_id, G4_PBO)
    g3 = ledger.latest_gate_results(campaign_id, G3_IS)
    ids = {t.candidate_id for t in trials}
    passed4 = sum(1 for cid in ids if g4.get(cid, (False, {}))[0] and g3.get(cid, (False, {}))[0])
    submitted = sum(
        1 for e, _i, _d in ledger.events_for(campaign_id, engine=engine, seed=seed)
        if e == Event.CANDIDATE_SUBMITTED
    )  # fmt: skip
    entries = load_entries(ledger, campaign_id, engine, seed, fmap)
    return {
        "engine": engine,
        "seed": seed,
        "trials": len(trials),
        "passed_gate4": passed4,
        "trial_efficiency": 100.0 * passed4 / len(trials) if trials else 0.0,
        "archive_cells": len({e.cell for e in entries}),
        "search_cells": len(set(trial_cells(ledger, campaign_id, engine, seed, fmap).values())),
        "proposals_per_trial": submitted / len(trials) if trials else None,
        "diverging": is_oos_diverging(checkpoints(ledger, campaign_id, engine, seed)),
    }
