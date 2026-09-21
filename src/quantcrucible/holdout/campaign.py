"""Holdout-side half of the campaign state machine: checks before opening, and FROZEN → BURNED
(Architecture §4.2, P6, ADR-0016).

Layer 1 (DB): ``holdout_claims`` is written in one transaction before any holdout byte is used —
one claim per campaign, per holdout manifest and per period, across campaigns — and
``holdout_access`` (one row per campaign) needs that claim. A claim is never undone: an error
after it still burns the campaign, recorded as FAIL.
Layer 2 (OS): the holdout files must match ``holdout.lock``, and ``holdout.lock`` must match the
hash the ledger recorded when the campaign opened. Layer 3 (process): only
:mod:`quantcrucible.holdout.evaluator_proc` calls this module.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from quantcrucible.config.lock import read_lock, sha256_file
from quantcrucible.data.holdout_split import verify_holdout
from quantcrucible.ledger.db import Ledger, LedgerError
from quantcrucible.ledger.records import Campaign, Event, GenerationEvent, HoldoutAccess
from quantcrucible.validation.freeze import Freeze, frozen_portfolio


class HoldoutRefused(RuntimeError):
    """The holdout may not be opened (state, tampering, missing threshold, second opening)."""


def find_frozen(ledger: Ledger, portfolio_hash: str) -> tuple[Campaign, Freeze]:
    """The FROZEN campaign whose frozen portfolio is ``portfolio_hash``."""
    for campaign in ledger.campaigns():
        freeze = frozen_portfolio(ledger, campaign.campaign_id)
        if freeze is not None and freeze.portfolio_hash == portfolio_hash:
            if campaign.status != "FROZEN":
                raise HoldoutRefused(f"campaign {campaign.campaign_id} is {campaign.status}")
            return campaign, freeze
    raise HoldoutRefused("no FROZEN campaign has this portfolio")


def preflight(
    ledger: Ledger,
    campaign: Campaign,
    freeze: Freeze,
    lock_path: Path,
    holdout_lock: Path,
    holdout_dir: Path,
    now: datetime,
) -> dict[str, Any]:
    """Every check that must pass before a single holdout byte is read. Returns the lock."""
    if ledger.holdout_access(campaign.campaign_id) is not None or ledger.holdout_claimed(
        campaign.campaign_id
    ):
        raise HoldoutRefused(f"the holdout of {campaign.campaign_id} was already opened")
    used = ledger.holdout_collision(campaign.holdout_range, campaign.holdout_lock_hash)
    if used is not None:
        raise HoldoutRefused(f"this holdout was already used (claimed {used}); never reused")
    if not freeze.frozen_at < now:
        raise HoldoutRefused("frozen_at must be before the opening")
    if not lock_path.is_file() or sha256_file(lock_path) != campaign.lock_hash:
        raise HoldoutRefused("evaluation lock missing or not the one recorded for the campaign")
    lock = read_lock(lock_path)
    if lock["campaign_id"] != campaign.campaign_id:
        raise HoldoutRefused("the evaluation lock belongs to another campaign")
    if lock["research"].get("holdout_pass") is None:
        raise HoldoutRefused("research.holdout_pass (D4) is not set: the holdout stays closed")
    if (
        campaign.holdout_lock_hash is None
        or sha256_file(holdout_lock) != campaign.holdout_lock_hash
    ):
        raise HoldoutRefused("holdout manifest differs from the one recorded at campaign open")
    try:
        verify_holdout(holdout_lock, holdout_dir)
    except Exception as e:  # any mismatch or missing file
        raise HoldoutRefused(f"holdout data failed verification: {e}") from e
    return lock


def claim(ledger: Ledger, campaign: Campaign, freeze: Freeze, at: datetime) -> None:
    """Take the one-time right to evaluate — BEFORE any holdout byte is used. Atomic in SQLite:
    of two concurrent evaluators, one gets :class:`HoldoutRefused`."""
    if campaign.holdout_lock_hash is None:
        raise HoldoutRefused("the campaign has no recorded holdout manifest")
    try:
        ledger.claim_holdout(
            campaign.campaign_id, freeze.portfolio_hash, campaign.holdout_range,
            campaign.holdout_lock_hash, at,
        )  # fmt: skip
    except LedgerError as e:
        raise HoldoutRefused(f"could not claim the holdout: {e}") from e
    ledger.log_event(
        GenerationEvent(
            run_id=f"holdout-{campaign.campaign_id}", campaign_id=campaign.campaign_id,
            engine="none", seed=0, agent="evaluator", model_used="none",
            event=Event.HOLDOUT_CLAIMED, detail={"portfolio_hash": freeze.portfolio_hash},
        )
    )  # fmt: skip


def burn_after_error(
    ledger: Ledger, campaign: Campaign, freeze: Freeze, at: datetime, error: BaseException
) -> None:
    """The claim was taken and something failed: the holdout stays consumed. Recorded as FAIL
    with no Sharpe; only the error *type* is logged (a message could carry holdout values)."""
    ledger.log_event(
        GenerationEvent(
            run_id=f"holdout-{campaign.campaign_id}", campaign_id=campaign.campaign_id,
            engine="none", seed=0, agent="evaluator", model_used="none",
            event=Event.HOLDOUT_EVALUATION_FAILED, detail={"error": type(error).__name__},
        )
    )  # fmt: skip
    burn(ledger, campaign, freeze, at, "FAIL", None)


def burn(
    ledger: Ledger,
    campaign: Campaign,
    freeze: Freeze,
    accessed_at: datetime,
    verdict: str,
    sharpe_oos: float | None,
) -> None:
    """Record the single opening (DB layer 1), then FROZEN → BURNED."""
    if verdict not in ("PASS", "FAIL"):
        raise ValueError("verdict must be PASS or FAIL")
    ledger.record_holdout_access(
        HoldoutAccess(
            campaign_id=campaign.campaign_id, portfolio_hash=freeze.portfolio_hash,
            frozen_at=freeze.frozen_at, accessed_at=accessed_at,
            timerange=campaign.holdout_range, verdict="PASS" if verdict == "PASS" else "FAIL",
            sharpe_oos=sharpe_oos,
        )
    )  # fmt: skip
    ledger.log_event(
        GenerationEvent(
            run_id=f"holdout-{campaign.campaign_id}", campaign_id=campaign.campaign_id,
            engine="none", seed=0, agent="evaluator", model_used="none",
            event=Event.HOLDOUT_OPENED, detail={"portfolio_hash": freeze.portfolio_hash},
        )
    )  # fmt: skip
    ledger.transition(campaign.campaign_id, "BURNED")
