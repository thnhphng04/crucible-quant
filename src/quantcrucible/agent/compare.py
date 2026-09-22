"""The phase-2 engine comparison: protocol locked before running, report, decision (arch §3.1.11
v0.6, ADR-0027, P2-15).

The protocol — metrics, the spread that counts as "beats", the decision rule — is written into
the campaign's audit log (``PROTOCOL_LOCKED``, with its hash) before the campaign's first trial;
``evolve`` refuses to run a harness-test campaign without it, and the report refuses a campaign
whose protocol does not predate its first trial (INV-70).

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
from quantcrucible.agent.monitor import engine_report
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent
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

PROTOCOL: dict[str, Any] = {
    "version": 1,
    "arms": ["gp", "random"],
    "primary_metric": "trial_efficiency = strategies passing gate 4 per 100 trials",
    "unit": "(engine, seed); >= 3 seeds; same trial quota per (engine, seed)",
    "beats": "mean(gp) - mean(random) > max(sd(gp), sd(random)), sample sd across seeds",
    "decision": {
        "gp_beats_random": "C-gp main engine; C-random stays as the permanent control",
        "tie": "C-random main engine; re-add GP components only after a dedicated ablation",
        "neither_meaningful": f"both arms < {MEANINGFUL_EFFICIENCY} per 100 trials: stop and "
        "find the cause in the DSL, the data or the gates",
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


def protocol_hash(protocol: dict[str, Any] = PROTOCOL) -> str:
    return hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()


def _locked_event_id(ledger: Ledger, campaign_id: str) -> int | None:
    return ledger.first_event_id(campaign_id, Event.PROTOCOL_LOCKED)


def lock_protocol(ledger: Ledger, campaign_id: str) -> str:
    """Record the protocol in the audit log — only while the campaign has no trial yet."""
    if ledger.trials(campaign_id):
        raise ProtocolError("the campaign already has trials: the protocol must come first")
    if _locked_event_id(ledger, campaign_id) is None:
        ledger.log_event(
            GenerationEvent(
                run_id="protocol", campaign_id=campaign_id, engine="compare", seed=0,
                agent="human", model_used="none", event=Event.PROTOCOL_LOCKED,
                detail={"protocol": PROTOCOL, "sha256": protocol_hash()},
            )
        )  # fmt: skip
    return protocol_hash()


def assert_protocol_predates_trials(ledger: Ledger, campaign_id: str) -> None:
    """INV-70: the protocol event exists and no submission of the campaign came before it."""
    locked = _locked_event_id(ledger, campaign_id)
    if locked is None:
        raise ProtocolError("no comparison protocol locked for this campaign (cli compare --lock)")
    first = ledger.first_event_id(campaign_id, Event.CANDIDATE_SUBMITTED)
    if first is not None and first < locked:
        raise ProtocolError("a candidate was submitted before the protocol was locked")


def _sd(values: Sequence[float]) -> float:
    return statistics.stdev(values) if len(values) > 1 else 0.0


def decide(efficiency: dict[str, list[float]]) -> dict[str, Any]:
    gp, rnd = efficiency.get("gp", []), efficiency.get("random", [])
    mean_gp = statistics.fmean(gp) if gp else 0.0
    mean_rnd = statistics.fmean(rnd) if rnd else 0.0
    spread = max(_sd(gp), _sd(rnd))
    if max(mean_gp, mean_rnd) < MEANINGFUL_EFFICIENCY:
        outcome = "neither_meaningful"
    elif mean_gp - mean_rnd > spread:
        outcome = "gp_beats_random"
    else:
        outcome = "tie"
    return {
        "mean_gp": mean_gp, "mean_random": mean_rnd, "spread": spread, "outcome": outcome,
        "action": PROTOCOL["decision"][outcome],
    }  # fmt: skip


def _backtests_per_pass(ledger: Ledger, campaign_id: str, engine: str) -> float | None:
    g4 = ledger.latest_gate_results(campaign_id, G4_PBO)
    ids = {t.candidate_id for t in ledger.trials(campaign_id, engine=engine)}
    configs = sum(int(g4[c][1].get("n_configs", 0)) for c in ids if c in g4)
    passed = sum(1 for c in ids if c in g4 and g4[c][0])
    return (configs + len(ids)) / passed if passed else None


def _engine_portfolio_dsr(session: ResearchSession, engine: str, fmap: FeatureMap) -> Any:
    """§3.2.1 from this engine's eligible trials only (cells from the feature map), recorded as
    a variant, DSR at the ledger's current N (the same for both arms)."""
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
    ppy = periods_per_year(session.timeframe)
    portfolio = build_portfolio(
        trials, {t.id: load_returns(t.returns_path) for t in trials},
        PortfolioRule.from_lock(session.lock), ledger.trial_stats(), ppy, cid,
    )  # fmt: skip
    record_variant(ledger, portfolio, session.results_dir)
    update_n_eff(ledger, seed=0)
    report = portfolio_dsr(
        portfolio.returns.to_numpy(), ledger.trial_stats(), ledger.total_portfolio_variants(), ppy
    )
    return {"members": len(portfolio.members), "dsr_n_eff": report.dsr_n_eff,
            "dsr_n_raw": report.dsr_n_raw}  # fmt: skip


def compare(session: ResearchSession, seeds: int, with_portfolios: bool = True) -> dict[str, Any]:
    ledger, cid = session.ledger, session.campaign_id
    assert_protocol_predates_trials(ledger, cid)
    fmap = FeatureMap.from_lock(session.lock)
    per_seed = {
        arm: [engine_report(ledger, cid, arm, s, fmap) for s in range(seeds)]
        for arm in PROTOCOL["arms"]
    }
    efficiency = {arm: [r["trial_efficiency"] for r in rows] for arm, rows in per_seed.items()}
    out: dict[str, Any] = {
        "campaign": cid,
        "protocol_sha256": protocol_hash(),
        "per_seed": per_seed,
        "decision": decide(efficiency),
        "gate4_backtests_per_passing_strategy": {
            arm: _backtests_per_pass(ledger, cid, arm) for arm in PROTOCOL["arms"]
        },
    }
    if with_portfolios:
        out["portfolio_dsr"] = {
            arm: _engine_portfolio_dsr(session, arm, fmap) for arm in PROTOCOL["arms"]
        }
    return out
