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
from quantcrucible.core.strategy.base import Bars, ScopeDirection
from quantcrucible.core.strategy.template import parse, template_hash
from quantcrucible.core.strategy.tunable import default_params
from quantcrucible.data.holdout_split import read_holdout_lock
from quantcrucible.data.manifest import verify_manifest
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
# Engine C's feature map (arch §3.1.3, P2-07): bounds fixed when a campaign opens, never
# stretched by observed values. Provisional ranges for daily crypto; categories = the grammar's.
FEATURE_MAP: dict[str, Any] = {
    "bins": 16,
    "bounds": {
        "trades_per_year": [0.0, 150.0],
        "max_drawdown": [0.0, 1.0],
        "sharpe_is": [-1.0, 3.0],
        "sortino_is": [-1.5, 4.5],
        "total_return": [-1.0, 4.0],
    },
    "categories": ["trend", "momentum", "mean_reversion", "breakout"],
}


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
        "sizing": {"rule": "risk_over_stop"},  # ADR-0031: Q = R/d, no vol-targeting knobs
        "pbo": {"n_splits": DEFAULT_SPLITS},
        "robustness": {"cost_multiplier": COST_MULTIPLIER, "max_sharpe_drop": MAX_SHARPE_DROP},
        "feature_map": FEATURE_MAP,
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
    perpetual = cfg.research.data.market == "usdt_m_perpetual"
    holdout_lock = root / "holdout" / "perp.lock" if perpetual else root / "holdout.lock"
    manifest = read_holdout_lock(holdout_lock)  # range + hashes only: no prices
    _check_trial_budget(cfg, ledger, str(manifest["range"]))
    base = "c-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    campaign_id, n = base, 1
    while ledger.campaign(campaign_id) is not None:  # two campaigns within one second
        n += 1
        campaign_id = f"{base}-{n}"
    derived = derived_settings(cfg.research.evolve_scope)
    if perpetual:
        is_dir = root / "data" / "perp"
        perp_manifest = is_dir / "manifest.json"
        data_manifest = verify_manifest(perp_manifest, is_dir)
        if data_manifest.source != "binanceusdm" or set(data_manifest.coverage) != set(
            cfg.research.data.symbols
        ):
            raise CampaignNotOpened("perpetual IS manifest has the wrong source or symbols")
        derived["perp_manifest_sha256"] = sha256_file(perp_manifest)
        if cfg.research.data.second_exchange is not None:
            second_dir = root / "data" / f"perp-second-{cfg.research.data.second_exchange}"
            second_path = second_dir / "manifest.json"
            second_manifest = verify_manifest(second_path, second_dir)
            if second_manifest.source != cfg.research.data.second_exchange or set(
                second_manifest.coverage
            ) != set(cfg.research.data.symbols):
                raise CampaignNotOpened("second perpetual IS manifest has wrong source or symbols")
            derived["second_perp_manifest_sha256"] = sha256_file(second_path)
    open_campaign(
        cfg, ledger, campaign_id, lock_path,
        holdout_range=str(manifest["range"]),
        holdout_lock_hash=sha256_file(holdout_lock),
        derived=derived,
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
    # The (instrument, direction) this candidate was searched for (P3-12). Unset for a legacy
    # whole-basket run, which is what every pre-P3 caller is.
    instrument: str | None = None
    direction: str | None = None
    island: str | None = None
    cell_id: str | None = None
    parents: tuple[str, ...] = ()
    mutation: str | None = None
    descriptors: Mapping[str, Any] | None = None
    agent: str = "engine"
    model_used: str = "none"
    trial_source: TrialSource = "evolution"


def _scope_direction(given: str | None, current: ScopeDirection) -> ScopeDirection:
    if given is None:
        return current
    if given not in ("long", "short"):
        raise ValueError(f"direction must be long or short, got {given!r}")
    return given  # type: ignore[return-value]


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
    # A scoped candidate sees ONE contract. Before P3-13 the universe was a campaign constant,
    # which made every candidate a whole-basket strategy; a BTC-long candidate measured on five
    # contracts is not the thing its scope searched for. An unscoped candidate keeps the basket,
    # because that is what the pre-P3 campaigns did and their trials must keep meaning it.
    scope = provenance.instrument if provenance else None
    if scope is not None and scope not in is_data:
        raise ValueError(
            f"instrument {scope!r} is not in this session's data ({sorted(is_data)}): refusing "
            "rather than falling back to the whole basket"
        )
    universe = (scope,) if scope is not None else tuple(is_data)
    scoped_bars = [is_data[s] for s in universe]
    first = min(b.ts[0] for b in scoped_bars).astype("datetime64[D]")
    last = max(b.ts[-1] for b in scoped_bars).astype("datetime64[D]")
    candidate = StrategyCandidate(
        candidate_id=candidate_id or f"manual-{strategy_hash(source)[:10]}-{stamp}",
        source=source,
        params=dict(params),
        universe=universe,
        timeframe=scoped_bars[0].timeframe,
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
        cell_id=p.cell_id, parents=p.parents, mutation=p.mutation, descriptors=p.descriptors,
        agent=p.agent, direction=_scope_direction(p.direction, candidate.direction),
        instrument=p.instrument,
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
