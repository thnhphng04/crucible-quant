"""Run one candidate through the phase-0 gates: ①a → ①b → ② → ③ (Architecture §3.2, §7).

Every run goes through the ledger (P2): the pipeline writes the submission, each gate result,
reject events and — from ③ on — the ``trials`` row.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from quantcrucible.config.lock import (
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
from quantcrucible.validation.archive import StrategyArchive
from quantcrucible.validation.gates import (
    GateContext,
    GatePipeline,
    PipelineOutcome,
    StrategyCandidate,
    strategy_hash,
)
from quantcrucible.validation.guardrail import DynamicGuardrail, StaticGuardrail
from quantcrucible.validation.is_gates import InSampleGate, MinBtlGate
from quantcrucible.validation.pbo import DEFAULT_SPLITS
from quantcrucible.validation.pbo_gate import PboGate
from quantcrucible.validation.sandbox import SandboxRunner

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
    }


def current_campaign(cfg: UserConfig, ledger: Ledger, lock_path: Path, root: Path) -> str:
    """The open campaign behind ``lock_path`` (verified), or a new one if there is none."""
    if lock_path.exists():
        campaign_id = str(read_lock(lock_path)["campaign_id"])
        assert_lock_matches(cfg, lock_path, ledger, campaign_id)
        return campaign_id
    holdout_lock = root / "holdout.lock"
    manifest = read_holdout_lock(holdout_lock)  # range + hashes only: no prices
    campaign_id = "c-" + datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    open_campaign(
        cfg, ledger, campaign_id, lock_path,
        holdout_range=str(manifest["range"]),
        holdout_lock_hash=sha256_file(holdout_lock),
        derived=derived_settings(cfg.research.evolve_scope),
    )  # fmt: skip
    return campaign_id


def make_candidate(
    source: str,
    campaign_id: str,
    is_data: Mapping[str, Bars],
    params: Mapping[str, float | int] | None = None,
    candidate_id: str | None = None,
    evolve_scope: str = "joint",
) -> StrategyCandidate:
    if params is None:
        params = default_params(list(parse(source).tunables))
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%f")
    first = min(b.ts[0] for b in is_data.values()).astype("datetime64[D]")
    last = max(b.ts[-1] for b in is_data.values()).astype("datetime64[D]")
    return StrategyCandidate(
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
