"""Run one candidate through the phase-0 gates: ①a → ①b → ② → ③ (Architecture §3.2, §7).

Every run goes through the ledger (P2): the pipeline writes the submission, each gate result,
reject events and — from ③ on — the ``trials`` row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from quantcrucible.config.lock import (
    CampaignNotOpened,
    assert_lock_matches,
    open_campaign,
    read_lock,
    sha256_file,
)
from quantcrucible.config.schema import UserConfig
from quantcrucible.core.sizing.vol_target import IDM_CAP, VOL_SPAN
from quantcrucible.core.strategy.base import Bars
from quantcrucible.core.strategy.template import parse, template_hash
from quantcrucible.core.strategy.tunable import default_params
from quantcrucible.data.holdout_split import read_holdout_lock
from quantcrucible.execution.nautilus_bridge import CostModel
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import TrialSource
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.gates import (
    GateContext,
    GatePipeline,
    PipelineOutcome,
    StrategyCandidate,
    strategy_hash,
)
from quantcrucible.validation.guardrail import DynamicGuardrail, StaticGuardrail
from quantcrucible.validation.is_gates import DAYS_PER_YEAR, InSampleGate, MinBtlGate
from quantcrucible.validation.pbo import DEFAULT_SPLITS
from quantcrucible.validation.pbo_gate import PboGate
from quantcrucible.validation.robustness import COST_MULTIPLIER, MAX_SHARPE_DROP
from quantcrucible.validation.sandbox import SandboxRunner
from quantcrucible.validation.statistical import max_trials_within

DEFAULT_LOOKBACK = 400
MAX_LEVERAGE = 1.0  # spot, cash account: gross exposure never above equity (ADR-0010)


def phase0_pipeline() -> GatePipeline:
    return GatePipeline([StaticGuardrail(), DynamicGuardrail(), MinBtlGate(), InSampleGate()])


def candidate_pipeline() -> GatePipeline:
    """Every per-candidate gate: ①a → ①b → ② → ③ → ④ (§3.2). ⑤ onward test the portfolio."""
    return GatePipeline(
        [StaticGuardrail(), DynamicGuardrail(), MinBtlGate(), InSampleGate(), PboGate()]
    )


def derived_settings(evolve_scope: str) -> dict[str, Any]:
    """Values fixed when a campaign opens, besides Group B: template hash, costs, lookback and
    the sizing constants of the Risk layer (ADR-0010)."""
    return {
        "template_hash": template_hash(evolve_scope),
        "costs": asdict(CostModel()),
        "lookback": DEFAULT_LOOKBACK,
        "sizing": {"vol_span": VOL_SPAN, "max_leverage": MAX_LEVERAGE, "idm_cap": IDM_CAP},
        "pbo": {"n_splits": DEFAULT_SPLITS},
        "robustness": {"cost_multiplier": COST_MULTIPLIER, "max_sharpe_drop": MAX_SHARPE_DROP},
    }


def current_campaign(cfg: UserConfig, ledger: Ledger, lock_path: Path, root: Path) -> str:
    """The campaign behind ``lock_path`` (verified) while it is OPEN or FROZEN; otherwise —
    no lock yet, or its campaign BURNED or ABANDONED — a new one.

    A new campaign needs ``research.holdout_pass`` (D4): it is locked with the campaign, so it
    must be chosen before the campaign's research starts, never after (ADR-0019)."""
    if lock_path.exists():
        campaign_id = str(read_lock(lock_path)["campaign_id"])
        campaign = ledger.campaign(campaign_id)
        if campaign is None or campaign.status in ("OPEN", "FROZEN"):
            assert_lock_matches(cfg, lock_path, ledger, campaign_id)
            return campaign_id
    if cfg.research.holdout_pass is None:
        raise CampaignNotOpened(
            "research.holdout_pass (D4) is not set in config/user.yaml: it is locked with the "
            "campaign, so decide it before a new campaign opens"
        )
    holdout_lock = root / "holdout.lock"
    manifest = read_holdout_lock(holdout_lock)  # range + hashes only: no prices
    _check_trial_budget(cfg, ledger, str(manifest["range"]))
    base = "c-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    campaign_id, n = base, 1
    while ledger.campaign(campaign_id) is not None:  # two campaigns within one second
        n += 1
        campaign_id = f"{base}-{n}"
    open_campaign(
        cfg, ledger, campaign_id, lock_path,
        holdout_range=str(manifest["range"]),
        holdout_lock_hash=sha256_file(holdout_lock),
        derived=derived_settings(cfg.research.evolve_scope),
    )  # fmt: skip
    return campaign_id


def _check_trial_budget(cfg: UserConfig, ledger: Ledger, holdout_range: str) -> None:
    """Refuse a campaign whose trial budget, on top of the ledger's N (N never resets), needs
    more IS history than there is before the holdout (gate ② at the locked target Sharpe)."""
    budget = cfg.research.campaign.trial_budget
    if budget is None:
        return
    is_end = date.fromisoformat(holdout_range[:10])
    years = (is_end - cfg.research.data.start).days / DAYS_PER_YEAR
    target = cfg.research.minbtl_target_sharpe
    allowed = max_trials_within(years, target)
    n_now = ledger.trial_stats().n_eff
    if n_now + budget > allowed:
        raise CampaignNotOpened(
            f"trial_budget {budget} + N {n_now} already in the ledger exceeds what MinBTL allows "
            f"for {years:.1f} years of IS data at target Sharpe {target:g} ({allowed} trials): "
            "lower the budget or the number of seeds"
        )


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where an engine's candidate comes from (P2-03): recorded in both the audit log and, once
    measured, its trial. ``parents`` are strategy hashes; ``mutation`` how it was bred."""

    engine: str
    seed: int
    run_id: str
    island: str | None = None
    cell_id: str | None = None
    parents: tuple[str, ...] = ()
    mutation: str | None = None
    agent: str = "engine"
    model_used: str = "none"
    trial_source: TrialSource = "evolution"


def make_candidate(
    source: str,
    campaign_id: str,
    is_data: Mapping[str, Bars],
    params: Mapping[str, float | int] | None = None,
    candidate_id: str | None = None,
    evolve_scope: str = "joint",
    provenance: Provenance | None = None,
) -> StrategyCandidate:
    if params is None:
        params = default_params(list(parse(source).tunables))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    first = min(b.ts[0] for b in is_data.values()).astype("datetime64[D]")
    last = max(b.ts[-1] for b in is_data.values()).astype("datetime64[D]")
    candidate = StrategyCandidate(
        candidate_id=candidate_id or f"manual-{strategy_hash(source)[:10]}-{stamp}",
        source=source,
        params=dict(params),
        universe=tuple(is_data),
        timeframe=next(iter(is_data.values())).timeframe,
        timerange=f"{first}/{last}",
        run_id=f"manual-{stamp}",
        campaign_id=campaign_id,
        evolve_scope=evolve_scope,
    )
    if provenance is None:
        return candidate
    p = provenance
    return replace(
        candidate, run_id=p.run_id, engine=p.engine, seed=p.seed, island=p.island,
        cell_id=p.cell_id, parents=p.parents, mutation=p.mutation, agent=p.agent,
        model_used=p.model_used, trial_source=p.trial_source,
    )  # fmt: skip


def run_candidate(
    candidate: StrategyCandidate,
    ledger: Ledger,
    lock: Mapping[str, Any],
    is_data: Mapping[str, Bars],
    sandbox: SandboxRunner,
    results_dir: Path,
    pipeline: GatePipeline | None = None,
) -> PipelineOutcome:
    services = {
        "sandbox": sandbox,
        "is_data": dict(is_data),
        "results_dir": results_dir,
        "archive": StrategyArchive(results_dir / "strategies"),
    }
    gates = pipeline or phase0_pipeline()
    return gates.run(candidate, GateContext(ledger, lock, services))
