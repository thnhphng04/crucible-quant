"""Run engine C on the open campaign (arch §3.1.11, ADR-0024, P2-09).

``evolve`` builds one engine per (engine, seed) quota of the campaign's trial budget, starts the
scheduler from the trials already in the ledger, and runs the evaluation pipeline until every
quota is used up (or an engine starves). The ledger is the only state: a second call continues
the same run — C-random replays its seeded sequence past the proposals it already submitted
(a proposal lost in flight by a crash may be evaluated once more; that only adds to ``N``).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from datetime import UTC, datetime

from quantcrucible.agent.compare import (
    ProtocolError,
    assert_protocol_predates_trials,
    divergence_rule,
    monitor_mode,
)
from quantcrucible.agent.engines.gp_search import GpSearch
from quantcrucible.agent.engines.random_search import RandomSearch
from quantcrucible.agent.evolution.archive import scope_args
from quantcrucible.agent.evolution.feature_map import FeatureMap
from quantcrucible.agent.monitor import EarlyStop, stopped_keys
from quantcrucible.agent.pipeline import (
    DEFAULT_WORKERS,
    Engine,
    Pipeline,
    RunStats,
    session_evaluator,
)
from quantcrucible.agent.runlock import RunLockError, campaign_run_lock
from quantcrucible.agent.scheduler import Key, TrialScheduler, campaign_scopes, quotas
from quantcrucible.ledger.records import Event
from quantcrucible.validation.pbo_gate import periods_per_year
from quantcrucible.validation.research_run import ResearchSession

ENGINE_SEED_BASE = {"random": 1, "gp": 2}  # distinct RNG streams per engine for the same seed
RUNNABLE = ("random", "gp")


class EvolveError(RuntimeError):
    """The campaign cannot run engines (no trial budget, unknown or deferred engine)."""


def engine_seed(
    engine: str, seed: int, instrument: str | None = None, direction: str | None = None
) -> int:
    """A distinct RNG stream per unit of search.

    Folding the scope in is not cosmetic: without it every scope of one (engine, seed) would
    draw the **same** sequence, so "independent search per scope" would be false at the RNG level
    while producing perfectly plausible results. The two sides of one contract must differ too.

    The two-argument form is unchanged, so runs recorded before P3-12 keep their streams.
    """
    base = 1_000_003 * ENGINE_SEED_BASE[engine] + seed
    if instrument is None and direction is None:
        return base
    scope = f"{instrument}|{direction}"
    return base ^ (int(hashlib.sha256(scope.encode()).hexdigest()[:12], 16) << 8)


def _submitted(session: ResearchSession, key: Key) -> int:
    events = session.ledger.events_for(session.campaign_id, *scope_args(key))
    return sum(1 for event, _island, _detail in events if event == Event.CANDIDATE_SUBMITTED)


def _engine(session: ResearchSession, key: Key, run_label: str) -> Engine:
    rng_seed = engine_seed(key.engine, key.seed, key.instrument, key.direction)
    if key.engine == "gp":
        gp_settings = session.lock["research"].get("gp", {})
        return GpSearch(
            session.ledger, session.campaign_id, key, rng_seed,
            FeatureMap.from_lock(session.lock), periods_per_year(session.timeframe),
            float(gp_settings.get("param_only_max", 0.30)), f"{run_label}-{key}",
        )  # fmt: skip
    if key.engine == "random":
        e = RandomSearch(seed=rng_seed)
        for _ in range(_submitted(session, key)):  # resume: replay what was already submitted
            e.next()
        return e
    raise EvolveError(f"engine {key.engine!r} is not runnable yet (runnable: {RUNNABLE})")


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
    all_quotas = quotas(budget, shares, int(research["seeds"]), campaign_scopes(session.lock))
    return {k: v for k, v in all_quotas.items() if k.engine in engines}


def evolve(
    session: ResearchSession,
    engines: Sequence[str],
    workers: int = DEFAULT_WORKERS,
    run_label: str | None = None,
) -> RunStats:
    limits = run_quotas(session, engines)
    purpose, _budget = session.ledger.campaign_purpose(session.campaign_id)
    if purpose == "harness_test":  # the comparison protocol comes before any trial (INV-70)
        try:
            assert_protocol_predates_trials(session.ledger, session.campaign_id)
        except ProtocolError as e:
            raise EvolveError(str(e)) from e
    label = run_label or "run-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    try:  # INV-72: the scheduler only sees the workers of its own run
        with campaign_run_lock(session.ledger.path, session.campaign_id, label):
            return _run_locked(session, limits, workers, label)
    except RunLockError as e:
        raise EvolveError(str(e)) from e


def _run_locked(
    session: ResearchSession, limits: dict[Key, int], workers: int, label: str
) -> RunStats:
    measured = {  # read under the lock: no other run is adding trials meanwhile
        k: len(session.ledger.trials(session.campaign_id, *scope_args(k))) for k in limits
    }
    mode = monitor_mode(session.ledger, session.campaign_id)
    rule = divergence_rule(session.ledger, session.campaign_id)
    pipeline = Pipeline(
        {k: _engine(session, k, label) for k in limits},
        TrialScheduler(limits, measured),
        session_evaluator(session, label),
        workers,
        label,
        on_abort=getattr(session.sandbox, "kill_all", None),
        monitor=EarlyStop(session.ledger, session.campaign_id, label, mode=mode, rule=rule),
        # ADR-0028: in warn mode nothing was stopped, so nothing is restored as stopped either
        stopped=stopped_keys(session.ledger, session.campaign_id, limits, rule)
        if mode == "stop"
        else (),
    )
    return pipeline.run()
