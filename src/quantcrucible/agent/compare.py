"""The phase-2 engine comparison: protocol locked before running, report, decision (arch §3.1.11
v0.6, ADR-0027, ADR-0028, P2-15).

The protocol — metrics, the spread that counts as "beats", the decision rule, and what the
monitor may do — is written into the campaign's audit log (``PROTOCOL_LOCKED``, with its hash)
before the campaign's first trial; ``evolve`` refuses to run a harness-test campaign without it,
and the report refuses a campaign whose protocol does not predate its first trial (INV-70). The
hash includes the monitor's thresholds and declared algorithm version directly (INV-76).
``monitor_fingerprint`` additionally checks behaviour on fixed checkpoint shapes; it cannot
detect every arithmetic rewrite, so such changes still require a version bump.

Primary metric: **trial efficiency** — strategies passing gate ④ per 100 trials — per
(engine, seed). C-gp beats C-random when the difference of the seed means exceeds the
seed-to-seed spread, taken as the larger of the two engines' sample standard deviations across
seeds. Supporting metrics (reported, not decisive): archive and search coverage, proposals per
trial, gate-④ backtests per passing strategy (CPU cost), IS→OOS divergence, and the DSR of a
portfolio built per §3.2.1 from each engine's candidates alone at the same ``N`` (each such
portfolio is recorded as a variant, as every built portfolio is). The raw IS Sharpe of the best
strategy is never compared (best-of-N).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import statistics
from collections.abc import Sequence
from typing import Any

from quantcrucible.agent.evolution.archive import trial_cells
from quantcrucible.agent.evolution.feature_map import FeatureMap, cell_id
from quantcrucible.agent.monitor import (
    CHECKPOINT_EVERY,
    MonitorMode,
    engine_report,
    warned_keys,
)
from quantcrucible.agent.scheduler import quotas
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent
from quantcrucible.validation.cpcv import (
    DEFAULT_DIVERGENCE,
    DegradationPoint,
    DivergenceRule,
    is_oos_diverging,
)
from quantcrucible.validation.gates import G4_PBO
from quantcrucible.validation.n_eff import update_n_eff
from quantcrucible.validation.pbo_gate import periods_per_year
from quantcrucible.validation.portfolio import (
    PortfolioRule,
    build_portfolio,
    eligible_trials,
    load_returns,
    record_variant,
)
from quantcrucible.validation.research_run import ResearchSession
from quantcrucible.validation.statistical import portfolio_dsr

MEANINGFUL_EFFICIENCY = 1.0  # passing ④ per 100 trials; below it for both arms ⇒ investigate
MIN_SEEDS = 3  # §3.1.11: the seed-to-seed spread needs at least three seeds

MONITOR_SCENARIOS: tuple[tuple[DegradationPoint, ...], ...] = tuple(
    tuple(DegradationPoint(f"p{k}", is_sr, oos) for k, (is_sr, oos) in enumerate(seq))
    for seq in (
        ((1.0, 0.8), (1.5, 0.5), (2.0, 0.2)),  # textbook divergence: IS up, OOS down
        ((1.0, 0.2), (1.5, 0.4), (2.0, 0.7)),  # both rising: healthy search
        ((1.0, 0.3), (1.0, 0.3), (1.0, 0.3)),  # flat line: arithmetic noise, never divergence
        ((1.0, 0.5), (1.5, 0.4), (1.5, 0.4)),  # one record change, then repeats (ADR-0028)
        ((1.0, 0.5), (1.0, 0.5), (1.5, 0.4)),  # repeats, then one record change
        ((1.0, 0.8), (1.5, 0.8), (2.0, 0.8)),  # IS up, OOS exactly flat
        ((1.0, 0.8), (1.5, 0.5)),  # below min_points: never a verdict
        ((2.0, 0.2), (1.5, 0.5), (1.0, 0.8)),  # IS falling: not the signal
        (),  # no checkpoints
        ((1.0, 0.8),),  # one checkpoint
        ((0.0, 0.0), (0.5e-9, -1.0), (1.0e-9, -2.0)),  # IS below tolerance
        ((0.0, 0.0), (2.0e-9, -1.0), (4.0e-9, -2.0)),  # IS above tolerance
        ((0.0, 0.0), (1.0, 0.5e-9), (2.0, 1.0e-9)),  # tiny OOS rise is flat
        ((0.0, 0.0), (1.0, 2.0e-9), (2.0, 4.0e-9)),  # material OOS rise
        ((1.0, 0.8), (1.5, 0.5), (2.0, 0.2), (2.5, 3.0)),  # recovery at point 4
        ((1.0, 0.8), (1.5, 0.5), (1.5, 0.5), (1.5, 0.5)),  # longer plateau
        # Endpoints rise, but OLS over all five OOS points falls: no endpoint shortcut.
        ((1.0, 0.0), (1.5, 4.0), (2.0, 0.0), (2.5, -4.0), (3.0, 1.0)),
    )
)
"""Fixed probes near the tolerance and across different curve shapes (ADR-0028).
These detect some behavioural changes, not every possible rewrite. Keep the values fixed
rather than deriving them from the active rule, which would move the probes with the threshold.
"""

PROTOCOL: dict[str, Any] = {
    "version": 3,
    "arms": ["gp", "random"],
    "primary_metric": "trial_efficiency = strategies passing gate 4 per 100 trials",
    "unit": "(engine, seed); >= 3 seeds; same trial quota per (engine, seed)",
    "beats": "mean(gp) - mean(random) > max(sd(gp), sd(random)), sample sd across seeds",
    "complete": "every (engine, seed) of every arm has used its whole quota; short of that the "
    "report shows progress and withholds the decision",
    "monitor": {
        "mode": "warn",
        "signal": "is_oos_diverging over DEGRADATION_CHECKPOINT points",
        "checkpoint_every": CHECKPOINT_EVERY,
        "note": "recorded as DEGRADATION_WARNING and reported; never stops an arm and never "
        "enters the decision (ADR-0028)",
    },
    "decision": {
        "gp_beats_random": "C-gp main engine; C-random stays as the permanent control",
        "tie": "C-random main engine; re-add GP components only after a dedicated ablation",
        "neither_meaningful": f"both arms < {MEANINGFUL_EFFICIENCY} per 100 trials: stop and "
        "find the cause in the DSL, the data or the gates",
        "incomplete": "progress only: the arms have not used their quotas, so the efficiencies "
        "are not comparable yet",
    },
    "supporting": [
        "archive_cells", "search_cells", "proposals_per_trial",
        "gate4_backtests_per_passing_strategy", "is_oos_diverging",
        "portfolio_dsr_per_engine (built per §3.2.1 from that engine alone, same N)",
    ],
    "never": "the raw IS Sharpe of the best strategy (best-of-N)",
}  # fmt: skip


class ProtocolError(RuntimeError):
    """The comparison protocol is missing or was locked too late."""


def monitor_fingerprint(rule: DivergenceRule = DEFAULT_DIVERGENCE) -> str:
    """What the divergence signal decides on the fixed shapes of ``MONITOR_SCENARIOS``.

    A **secondary** check only: the thresholds themselves are hashed as data (``monitor.rule``),
    which is what binds a campaign. The fingerprint catches a rewrite that keeps the same
    thresholds but changes the arithmetic, on the shapes it covers — and only those, so it can
    never be the lock (ADR-0028 amendment).
    """
    verdicts = [is_oos_diverging(points, rule) for points in MONITOR_SCENARIOS]
    return hashlib.sha256(json.dumps(verdicts).encode()).hexdigest()


def protocol() -> dict[str, Any]:
    """The protocol as it is locked and compared: the dict, the divergence rule the monitor will
    actually run with, and the fingerprint of that rule's behaviour."""
    monitor = {**PROTOCOL["monitor"], "rule": dataclasses.asdict(DEFAULT_DIVERGENCE)}
    return {
        **PROTOCOL,
        "monitor": monitor,
        "monitor_fingerprint": monitor_fingerprint(DEFAULT_DIVERGENCE),
    }


def divergence_rule(ledger: Ledger, campaign_id: str) -> DivergenceRule:
    """The rule this campaign locked, so the monitor is read with the thresholds the campaign was
    run under. A campaign with no protocol keeps the code's default."""
    locked = (locked_protocol(ledger, campaign_id) or {}).get("protocol", {})
    raw = locked.get("monitor", {}).get("rule")
    return DivergenceRule.from_locked(raw) if raw else DEFAULT_DIVERGENCE


def protocol_hash(p: dict[str, Any] | None = None) -> str:
    return hashlib.sha256(json.dumps(p or protocol(), sort_keys=True).encode()).hexdigest()


def _locked_event_id(ledger: Ledger, campaign_id: str) -> int | None:
    return ledger.first_event_id(campaign_id, Event.PROTOCOL_LOCKED)


def locked_protocol(ledger: Ledger, campaign_id: str) -> dict[str, Any] | None:
    """What this campaign locked — not what the code says today."""
    for event, _island, detail in ledger.events_for(campaign_id):
        if event == Event.PROTOCOL_LOCKED and detail:
            return dict(detail)
    return None


def assert_protocol_is_the_locked_one(ledger: Ledger, campaign_id: str) -> str:
    """The campaign is measured by the protocol it locked. A campaign locked under an earlier
    protocol is never re-read under a newer one: it needs its own run (ADR-0027)."""
    locked = locked_protocol(ledger, campaign_id)
    if locked is None:
        raise ProtocolError("no comparison protocol locked for this campaign (cli compare --lock)")
    stored = str(locked.get("sha256", ""))
    if stored != protocol_hash():
        was = locked.get("protocol", {}).get("version", "?")
        raise ProtocolError(
            f"this campaign locked protocol v{was} (sha256 {stored[:12]}), the code now holds "
            f"v{PROTOCOL['version']} (sha256 {protocol_hash()[:12]}): report it with the code it "
            "was run under, or run the new protocol in a new campaign"
        )
    return stored


def lock_protocol(ledger: Ledger, campaign_id: str) -> str:
    """Record the protocol in the audit log — only while the campaign has no trial yet."""
    if ledger.trials(campaign_id):
        raise ProtocolError("the campaign already has trials: the protocol must come first")
    locked = locked_protocol(ledger, campaign_id)
    if locked is not None and str(locked.get("sha256", "")) != protocol_hash():
        raise ProtocolError(
            f"this campaign already locked another protocol (sha256 "
            f"{str(locked.get('sha256', ''))[:12]}): a changed protocol needs a new campaign"
        )
    if _locked_event_id(ledger, campaign_id) is None:
        ledger.log_event(
            GenerationEvent(
                run_id="protocol", campaign_id=campaign_id, engine="compare", seed=0,
                agent="human", model_used="none", event=Event.PROTOCOL_LOCKED,
                detail={"protocol": protocol(), "sha256": protocol_hash()},
            )
        )  # fmt: skip
    return protocol_hash()


def monitor_mode(ledger: Ledger, campaign_id: str) -> MonitorMode:
    """How this campaign's locked protocol lets the monitor act. A campaign with no protocol —
    any campaign that is not an engine comparison — keeps the ordinary early stop."""
    locked = locked_protocol(ledger, campaign_id)
    mode = (locked or {}).get("protocol", {}).get("monitor", {}).get("mode")
    return "warn" if mode == "warn" else "stop"


def assert_protocol_predates_trials(ledger: Ledger, campaign_id: str) -> None:
    """INV-70: the protocol event exists, matches the one in the code, and no submission of the
    campaign came before it."""
    assert_protocol_is_the_locked_one(ledger, campaign_id)
    locked = _locked_event_id(ledger, campaign_id)
    first = ledger.first_event_id(campaign_id, Event.CANDIDATE_SUBMITTED)
    if locked is not None and first is not None and first < locked:
        raise ProtocolError("a candidate was submitted before the protocol was locked")


def _sd(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def decide(
    efficiency: dict[str, list[float]],
    unfinished: Sequence[str] = (),
) -> dict[str, Any]:
    """The locked rule. ``unfinished`` are the (engine, seed) units short of their quota: their
    efficiencies were measured at a different budget, so the engine decision is withheld.

    A divergence warning never reaches this function — under protocol v3 the monitor cannot cut
    an arm short, and the warning is reported beside the decision, not inside it (ADR-0028)."""
    gp, rnd = efficiency.get("gp", []), efficiency.get("random", [])
    mean_gp = statistics.fmean(gp) if gp else 0.0
    mean_rnd = statistics.fmean(rnd) if rnd else 0.0
    spread = max(_sd(gp), _sd(rnd))
    if unfinished:
        outcome = "incomplete"
    elif max(mean_gp, mean_rnd) < MEANINGFUL_EFFICIENCY:
        outcome = "neither_meaningful"
    elif mean_gp - mean_rnd > spread:
        outcome = "gp_beats_random"
    else:
        outcome = "tie"
    out = {
        "mean_gp": mean_gp, "mean_random": mean_rnd, "spread": spread, "outcome": outcome,
        "action": PROTOCOL["decision"][outcome],
    }  # fmt: skip
    if unfinished:
        out["unfinished"] = list(unfinished)
    return out


def _completeness(session: ResearchSession, seeds: int) -> dict[str, Any]:
    """Trials against quota for every (engine, seed) of both arms, from the ledger and the
    campaign's locked budget — the protocol compares arms at the same quota."""
    ledger, cid = session.ledger, session.campaign_id
    _purpose, budget = ledger.campaign_purpose(cid)
    shares = {e: float(s) for e, s in session.lock["research"]["engines"].items()}
    limits = quotas(budget, shares, seeds) if budget else {}
    rows: dict[str, Any] = {}
    unfinished: list[str] = []
    for arm in PROTOCOL["arms"]:
        for seed in range(seeds):
            quota = limits.get((arm, seed))
            done = len(ledger.trials(cid, engine=arm, seed=seed))
            rows[f"{arm}-s{seed}"] = {"trials": done, "quota": quota}
            if quota is None or done < quota:
                unfinished.append(f"{arm}-s{seed}")
    if seeds < MIN_SEEDS:
        unfinished.append(f"seeds={seeds} < {MIN_SEEDS}")
    return {"per_unit": rows, "unfinished": unfinished}


def _backtests_per_pass(ledger: Ledger, campaign_id: str, engine: str) -> float | None:
    g4 = ledger.latest_gate_results(campaign_id, G4_PBO)
    ids = {t.candidate_id for t in ledger.trials(campaign_id, engine=engine)}
    configs = sum(int(g4[c][1].get("n_configs", 0)) for c in ids if c in g4)
    passed = sum(1 for c in ids if c in g4 and g4[c][0])
    return (configs + len(ids)) / passed if passed else None


def _engine_portfolio(session: ResearchSession, engine: str, fmap: FeatureMap) -> Any:
    """§3.2.1 from this engine's eligible trials only (cells from the feature map), recorded as
    a variant like every built portfolio."""
    ledger, cid = session.ledger, session.campaign_id
    trials = [t for t in eligible_trials(ledger, cid) if t.engine == engine]
    if not trials:
        return None
    cells: dict[str, str] = {}
    for seed in {t.seed for t in trials}:
        cells.update(
            {k: cell_id(v) for k, v in trial_cells(ledger, cid, engine, seed, fmap).items()}
        )
    trials = [dataclasses.replace(t, cell_id=cells.get(t.candidate_id)) for t in trials]
    portfolio = build_portfolio(
        trials, {t.id: load_returns(t.returns_path) for t in trials},
        PortfolioRule.from_lock(session.lock), ledger.trial_stats(),
        periods_per_year(session.timeframe), cid,
    )  # fmt: skip
    record_variant(ledger, portfolio, session.results_dir)
    return portfolio


def _portfolio_dsr_per_engine(
    session: ResearchSession, arms: Sequence[str], fmap: FeatureMap
) -> dict[str, Any]:
    """Both portfolios first, then both DSRs against one snapshot of the ledger's statistics:
    recording a variant raises the variant count, so computing as we go would deflate the second
    arm harder than the first."""
    ledger = session.ledger
    built = {arm: _engine_portfolio(session, arm, fmap) for arm in arms}
    update_n_eff(ledger, seed=0)
    stats, variants = ledger.trial_stats(), ledger.total_portfolio_variants()
    ppy = periods_per_year(session.timeframe)
    out: dict[str, Any] = {"n_eff": stats.n_eff, "portfolio_variants": variants}
    for arm, portfolio in built.items():
        if portfolio is None:
            out[arm] = None
            continue
        report = portfolio_dsr(portfolio.returns.to_numpy(), stats, variants, ppy)
        out[arm] = {"members": len(portfolio.members), "dsr_n_eff": report.dsr_n_eff,
                    "dsr_n_raw": report.dsr_n_raw}  # fmt: skip
    return out


def compare(session: ResearchSession, seeds: int, with_portfolios: bool = True) -> dict[str, Any]:
    ledger, cid = session.ledger, session.campaign_id
    assert_protocol_predates_trials(ledger, cid)
    fmap = FeatureMap.from_lock(session.lock)
    rule = divergence_rule(ledger, cid)  # the campaign is read with the rule it locked
    per_seed = {
        arm: [engine_report(ledger, cid, arm, s, fmap, rule) for s in range(seeds)]
        for arm in PROTOCOL["arms"]
    }
    efficiency = {arm: [r["trial_efficiency"] for r in rows] for arm, rows in per_seed.items()}
    completeness = _completeness(session, seeds)
    # from the ledger, not from the current curve: a warning is an event that happened, and an
    # arm whose OOS later recovers must not drop out of the report (ADR-0028 amendment)
    keys = [(arm, s) for arm in PROTOCOL["arms"] for s in range(seeds)]
    warnings = sorted(f"{arm}-s{s}" for arm, s in warned_keys(ledger, cid, keys))
    out: dict[str, Any] = {
        "campaign": cid,
        "protocol_sha256": protocol_hash(),
        "per_seed": per_seed,
        "completeness": completeness,
        "divergence_warnings": warnings,  # reported, never decisive (ADR-0028)
        "decision": decide(efficiency, completeness["unfinished"]),
        "gate4_backtests_per_passing_strategy": {
            arm: _backtests_per_pass(ledger, cid, arm) for arm in PROTOCOL["arms"]
        },
    }
    if with_portfolios:
        out["portfolio_dsr"] = _portfolio_dsr_per_engine(session, PROTOCOL["arms"], fmap)
    return out
