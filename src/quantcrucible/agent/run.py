"""Run engine C on the open campaign (arch §3.1.11, ADR-0024, P2-09).

``evolve`` builds one engine per (engine, seed) quota of the campaign's trial budget, starts the
scheduler from the trials already in the ledger, and runs the evaluation pipeline until every
quota is used up (or an engine starves). The ledger is the only state: a second call continues
the same run — C-random replays its seeded sequence past the proposals it already submitted
(a proposal lost in flight by a crash may be evaluated once more; that only adds to ``N``).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from quantcrucible.agent.engines.gp_search import GpSearch
from quantcrucible.agent.engines.random_search import RandomSearch
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.pipeline import (
    DEFAULT_WORKERS,
    Engine,
    Pipeline,
    RunStats,
    session_evaluator,
)
from quantcrucible.agent.scheduler import Key, TrialScheduler, quotas
from quantcrucible.ledger.records import Event
from quantcrucible.validation.pbo_gate import periods_per_year
from quantcrucible.validation.research_run import ResearchSession

ENGINE_SEED_BASE = {"random": 1, "gp": 2}  # distinct RNG streams per engine for the same seed
RUNNABLE = ("random", "gp")


class EvolveError(RuntimeError):
    """The campaign cannot run engines (no trial budget, unknown or deferred engine)."""


def engine_seed(engine: str, seed: int) -> int:
    return 1_000_003 * ENGINE_SEED_BASE[engine] + seed


def _submitted(session: ResearchSession, key: Key) -> int:
    engine, seed = key
    events = session.ledger.events_for(session.campaign_id, engine=engine, seed=seed)
    return sum(1 for event, _island, _detail in events if event == Event.CANDIDATE_SUBMITTED)


def _engine(session: ResearchSession, key: Key, run_label: str) -> Engine:
    engine, seed = key
    if engine == "gp":
        gp_settings = session.lock["research"].get("gp", {})
        return GpSearch(
            session.ledger, session.campaign_id, seed, engine_seed(engine, seed),
            FeatureMap.from_lock(session.lock), periods_per_year(session.timeframe),
            float(gp_settings.get("param_only_max", 0.30)), f"{run_label}-{engine}-s{seed}",
        )  # fmt: skip
    if engine == "random":
        e = RandomSearch(seed=engine_seed(engine, seed))
        for _ in range(_submitted(session, key)):  # resume: replay what was already submitted
            e.next()
        return e
    raise EvolveError(f"engine {engine!r} is not runnable yet (runnable: {RUNNABLE})")


def run_quotas(session: ResearchSession, engines: Sequence[str]) -> dict[Key, int]:
    _purpose, budget = session.ledger.campaign_purpose(session.campaign_id)
    if budget is None:
        raise EvolveError(
            "the campaign has no trial budget: set research.campaign.trial_budget (and purpose "
            "harness_test for the phase-2 comparison) before a campaign opens"
        )
    research = session.lock["research"]
    shares = {e: float(s) for e, s in research["engines"].items()}
    unknown = set(engines) - set(shares)
    if unknown:
        raise EvolveError(f"unknown engine(s) {sorted(unknown)}")
    idle = [e for e in engines if shares[e] <= 0]
    if idle:
        raise EvolveError(f"engine(s) {idle} have no budget share in this campaign")
    all_quotas = quotas(budget, shares, int(research["seeds"]))
    return {k: v for k, v in all_quotas.items() if k[0] in engines}


def evolve(
    session: ResearchSession,
    engines: Sequence[str],
    workers: int = DEFAULT_WORKERS,
    run_label: str | None = None,
) -> RunStats:
    limits = run_quotas(session, engines)
    measured = {
        k: len(session.ledger.trials(session.campaign_id, engine=k[0], seed=k[1])) for k in limits
    }
    label = run_label or "run-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    pipeline = Pipeline(
        {k: _engine(session, k, label) for k in limits},
        TrialScheduler(limits, measured),
        session_evaluator(session, label),
        workers,
        label,
        on_abort=getattr(session.sandbox, "kill_all", None),
    )
    return pipeline.run()
