"""The phase-1 research workflow, before the freeze (Architecture §3.2, §3.2.1, §7 phase 1,
ADR-0018):

1. every candidate through ①a → ①b → ② → ③ → ④ (:func:`submit`);
2. portfolio built by the pre-registered rule, then ⑤ → ⑥′ (:func:`evaluate_portfolio`);
3. if calibration is enabled, step 5b for each member (:func:`calibrate_members`), then the
   portfolio is rebuilt — a new variant — and ⑤ → ⑥′ run again.

The freeze (:mod:`quantcrucible.validation.freeze`) and the holdout (its own process) come after.
Every backtest here goes through a gate pipeline and so through the ledger (P2).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pandas as pd

from quantcrucible.core.perp_inputs import PerpBundle
from quantcrucible.core.strategy.base import Bars
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.execution.risk import RiskSettings
from quantcrucible.ledger.db import Ledger
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.calibration import (
    CalibrationResult,
    calibrate,
    calibration_settings,
    check_allowed,
)
from quantcrucible.validation.gates import GateContext, PipelineOutcome, StrategyCandidate
from quantcrucible.validation.is_gates import signals_path_for
from quantcrucible.validation.pbo_gate import periods_per_year
from quantcrucible.validation.portfolio import (
    Member,
    Portfolio,
    PortfolioRule,
    build_and_record,
    consolidate_on_account,
    eligible_trials,
    load_returns,
    load_signal_stream,
    record_variant,
    select_slots,
    write_account_curve,
)
from quantcrucible.validation.portfolio_dsr import DsrGate, PortfolioOutcome, PortfolioPipeline
from quantcrucible.validation.robustness import RobustnessGate
from quantcrucible.validation.run import Provenance, candidate_pipeline, make_candidate
from quantcrucible.validation.sandbox import JobRunner


@dataclass
class ResearchSession:
    ledger: Ledger
    lock: Mapping[str, Any]
    campaign_id: str
    is_data: Mapping[str, Bars]
    sandbox: JobRunner
    results_dir: Path
    second_is_data: Mapping[str, Bars] = field(default_factory=dict)
    perp_data: Mapping[str, PerpBundle] = field(default_factory=dict)
    second_perp_data: Mapping[str, PerpBundle] = field(default_factory=dict)

    @property
    def archive(self) -> StrategyArchive:
        return StrategyArchive(self.results_dir / "strategies")

    def context(self) -> GateContext:
        services: dict[str, Any] = {
            "sandbox": self.sandbox,
            "is_data": dict(self.is_data),
            "second_is_data": dict(self.second_is_data),
            "perp_data": dict(self.perp_data),
            "second_perp_data": dict(self.second_perp_data),
            "results_dir": self.results_dir,
            "archive": self.archive,
        }
        return GateContext(self.ledger, self.lock, services)

    @property
    def timeframe(self) -> str:
        return next(iter(self.is_data.values())).timeframe


def submit(
    session: ResearchSession,
    source: str,
    candidate_id: str,
    params: Mapping[str, float | int] | None = None,
    provenance: Provenance | None = None,
) -> PipelineOutcome:
    """Take one candidate through ①a → ④ (the only way an engine's strategy is evaluated)."""
    evolve_scope = str(session.lock["research"].get("evolve_scope", "joint"))
    candidate = make_candidate(
        source, session.campaign_id, session.is_data, params=params, candidate_id=candidate_id,
        evolve_scope=evolve_scope, provenance=provenance,
    )  # fmt: skip
    return candidate_pipeline().run(candidate, session.context())


def evaluate_portfolio(session: ResearchSession) -> tuple[Portfolio, PortfolioOutcome]:
    """Build by the locked rule (a variant row if new), then ⑤ → ⑥′."""
    rule = PortfolioRule.from_lock(session.lock)
    ppy = periods_per_year(session.timeframe)
    if session.lock["research"].get("data", {}).get("market") == "usdt_m_perpetual":
        if not session.perp_data:
            raise ValueError("perpetual portfolio needs aligned mark, funding, path and brackets")
        trials = eligible_trials(session.ledger, session.campaign_id)
        trial_returns = {t.id: load_returns(t.returns_path) for t in trials}
        chosen = select_slots(
            trials, trial_returns, session.ledger.trial_stats(), ppy, rule.max_strategies
        )
        if not chosen:
            raise ValueError("no positive gate-④ candidate for any perpetual slot")
        streams = {t.id: signals_path_for(Path(t.returns_path)) for t in chosen}
        for trial in chosen:
            plans = load_signal_stream(streams[trial.id])
            if len(plans) != 1 or plans[0].slot != (trial.instrument, trial.direction):
                raise ValueError(
                    f"trial {trial.id}: signal stream does not match its locked "
                    f"{trial.instrument}/{trial.direction} slot"
                )
        members = tuple(
            Member(
                t.id,
                t.candidate_id,
                t.strategy_hash,
                dict(t.params),
                1.0 / len(chosen),
                (t.instrument,),
                t.timeframe,
                t.direction,
            )
            for t in chosen
        )
        research = session.lock["research"]
        replay = consolidate_on_account(
            members,
            streams,
            session.is_data,
            session.perp_data,
            settings=RiskSettings(max_risk_pct=float(research["max_risk_pct"])),
            costs=CostModel(**session.lock["derived"]["costs"]),
            initial_cash=100_000.0,
            leverage=int(research["data"]["leverage"]),
            max_portfolio_risk_pct=0.10,
        )
        portfolio = Portfolio(
            session.campaign_id,
            replace(rule, selection="slot_psr", weighting="risk_per_slot", rebalance="none"),
            members,
            pd.Series(replay.returns, index=pd.to_datetime(replay.ts[1:])),
            ppy,
            {
                "eligible": [t.candidate_id for t in trials],
                "slots": [t.candidate_id for t in chosen],
            },
        )
        write_account_curve(
            session.results_dir,
            session.campaign_id,
            portfolio.portfolio_hash,
            ts=replay.ts,
            equity=replay.equity,
            initial_cash=100_000.0,
            contributions=replay.contributions,
        )
        record_variant(session.ledger, portfolio, session.results_dir)
    else:
        portfolio = build_and_record(
            session.ledger, session.campaign_id, rule, ppy, session.results_dir,
        )  # fmt: skip
    pipeline = PortfolioPipeline([DsrGate(), RobustnessGate()])
    return portfolio, pipeline.run(portfolio, session.context())


def calibrate_members(session: ResearchSession, portfolio: Portfolio) -> list[CalibrationResult]:
    """Step 5b for every member, from its archived source and its trial's parameters. Every
    member is checked against the once-per-campaign rule before any of them runs: a refusal
    (:class:`CalibrationRefused`) leaves nothing half-done."""
    enabled, budget = calibration_settings(session.lock)
    if not enabled:
        return []
    candidates = [_member_candidate(session, m) for m in portfolio.members]
    ctx = session.context()
    for c in candidates:
        check_allowed(ctx, c, budget)
    return [calibrate(c, session.context(), budget, candidate_pipeline()) for c in candidates]


def _member_candidate(session: ResearchSession, m: Member) -> StrategyCandidate:
    return StrategyCandidate(
        candidate_id=m.candidate_id, source=session.archive.get(m.strategy_hash),
        params=dict(m.params), universe=m.universe, timeframe=m.timeframe,
        timerange=_timerange(session.is_data), run_id=f"calib-{m.candidate_id}",
        campaign_id=session.campaign_id,
        evolve_scope=str(session.lock["research"].get("evolve_scope", "joint")),
        trial_source="param_opt",
    )  # fmt: skip


def _timerange(is_data: Mapping[str, Bars]) -> str:
    first = min(b.ts[0] for b in is_data.values()).astype("datetime64[D]")
    last = max(b.ts[-1] for b in is_data.values()).astype("datetime64[D]")
    return f"{first}/{last}"


@dataclass(frozen=True, slots=True)
class Phase1Result:
    candidates: dict[str, PipelineOutcome]
    first: tuple[Portfolio, PortfolioOutcome]
    calibration: list[CalibrationResult]
    final: tuple[Portfolio, PortfolioOutcome]


def run_phase1(session: ResearchSession, sources: Mapping[str, str]) -> Phase1Result:
    outcomes = {name: submit(session, src, name) for name, src in sources.items()}
    first = evaluate_portfolio(session)
    calibrated = calibrate_members(session, first[0])
    final = evaluate_portfolio(session) if calibrated else first
    return Phase1Result(outcomes, first, calibrated, final)
