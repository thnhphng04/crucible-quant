"""Freeze a campaign on one validated portfolio: OPEN → FROZEN (Architecture §4.2 step 2,
ADR-0016).

Research-side half of the campaign state machine. It refuses unless the portfolio is a recorded
variant of this campaign whose latest gate-⑤ and gate-⑥′ results are passes **computed on the
ledger as it is now**: every trial or portfolio variant added since raises ``N`` and so lowers
DSR, which makes those results stale (re-run ⑤ → ⑥′ first). The frozen
``portfolio_hash`` and the freeze time go to the audit log (``CAMPAIGN_FROZEN``); from then on
the candidate pipeline and the portfolio builder refuse to add anything to the campaign. Opening
the holdout (FROZEN → BURNED) belongs to the separate evaluator process only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event, GenerationEvent, utc_now
from quantcrucible.validation.portfolio_dsr import G5_DSR, G6P_ROBUSTNESS, SNAPSHOT_KEY


class FreezeError(RuntimeError):
    """The campaign cannot be frozen on this portfolio."""


@dataclass(frozen=True, slots=True)
class Freeze:
    campaign_id: str
    portfolio_hash: str
    frozen_at: datetime


def _passed(ledger: Ledger, p_hash: str, gate: str) -> bool:
    results = [(g, ok) for g, ok, _ in ledger.gate_results(p_hash) if g == gate]
    return bool(results) and results[-1][1]


def _current(ledger: Ledger, p_hash: str, gate: str) -> bool:
    details = ledger.gate_result_details(p_hash, gate)
    return bool(details) and details[-1].get(SNAPSHOT_KEY) == ledger.snapshot()


def freeze_campaign(ledger: Ledger, campaign_id: str, portfolio_hash: str) -> Freeze:
    campaign = ledger.campaign(campaign_id)
    if campaign is None:
        raise FreezeError(f"unknown campaign {campaign_id!r}")
    if campaign.status != "OPEN":
        raise FreezeError(f"campaign {campaign_id} is {campaign.status}, not OPEN")
    if portfolio_hash not in {v.portfolio_hash for v in ledger.portfolio_variants(campaign_id)}:
        raise FreezeError(f"{portfolio_hash[:12]}… is not a portfolio variant of {campaign_id}")
    missing = [g for g in (G5_DSR, G6P_ROBUSTNESS) if not _passed(ledger, portfolio_hash, g)]
    if missing:
        raise FreezeError(f"the portfolio has not passed {', '.join(missing)}")
    stale = [g for g in (G5_DSR, G6P_ROBUSTNESS) if not _current(ledger, portfolio_hash, g)]
    if stale:
        raise FreezeError(
            f"{', '.join(stale)} ran on an older ledger (trials or variants were added since, "
            f"now {ledger.snapshot()}): re-run ⑤ → ⑥′ on this portfolio before freezing"
        )
    frozen_at = utc_now()
    ledger.transition(campaign_id, "FROZEN")
    ledger.log_event(
        GenerationEvent(
            run_id=f"freeze-{campaign_id}", campaign_id=campaign_id, engine="none", seed=0,
            agent="human", model_used="none", event=Event.CAMPAIGN_FROZEN,
            detail={"portfolio_hash": portfolio_hash, "frozen_at": frozen_at.isoformat()},
        )
    )  # fmt: skip
    return Freeze(campaign_id, portfolio_hash, frozen_at)


def abandon(ledger: Ledger, campaign_id: str, reason: str) -> None:
    """OPEN → ABANDONED without opening the holdout (ADR-0019): an audit event, then the
    append-only abandonment row. The campaign's trials stay in ``N``; its lock stays as it is
    and is archived unchanged when the next campaign opens; its holdout was never claimed."""
    campaign = ledger.campaign(campaign_id)
    if campaign is None:
        raise FreezeError(f"unknown campaign {campaign_id!r}")
    if campaign.status != "OPEN":
        raise FreezeError(f"campaign {campaign_id} is {campaign.status}: only OPEN is abandoned")
    if not reason.strip():
        raise FreezeError("abandoning a campaign needs a reason")
    ledger.log_event(
        GenerationEvent(
            run_id=f"abandon-{campaign_id}", campaign_id=campaign_id, engine="none", seed=0,
            agent="human", model_used="none", event=Event.CAMPAIGN_ABANDONED,
            detail={"reason": reason},
        )
    )  # fmt: skip
    ledger.abandon_campaign(campaign_id, reason)


def frozen_portfolio(ledger: Ledger, campaign_id: str) -> Freeze | None:
    """The freeze record of a FROZEN or BURNED campaign, from the audit log."""
    for event, detail in reversed(ledger.event_details(campaign_id)):
        if event == Event.CAMPAIGN_FROZEN and detail:
            return Freeze(
                campaign_id,
                str(detail["portfolio_hash"]),
                datetime.fromisoformat(str(detail["frozen_at"])),
            )
    return None
