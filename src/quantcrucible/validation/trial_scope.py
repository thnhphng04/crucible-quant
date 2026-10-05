"""Which trials a campaign's statistics count (P3-63, ADR-0050, D27).

A campaign locked with ``derived.trial_scope: campaign_v1`` counts only its own trials: gate ②'s
``N``, gate ⑤'s ``N_raw``/``N_eff``/``V[SR]`` and portfolio variants, gate ⑥′, the ranking's
benchmark, the trial budget and the snapshot the freeze compares. Every trial is still recorded
in the one append-only ledger and none is ever removed (P2). A lock without the key keeps
counting the whole ledger, as every campaign did before.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CAMPAIGN_V1 = "campaign_v1"
TRIAL_SCOPES = (CAMPAIGN_V1,)


def trial_scope(lock: Mapping[str, Any], campaign_id: str) -> str | None:
    """The campaign whose trials ``campaign_id``'s statistics count, or ``None`` for the whole
    ledger (a lock without the key)."""
    tag = lock.get("derived", {}).get("trial_scope")
    if tag is None:
        return None
    if tag not in TRIAL_SCOPES:
        raise ValueError(f"unknown locked trial scope {tag!r}")
    locked = lock.get("campaign_id")
    if locked is not None and locked != campaign_id:
        raise ValueError(f"lock of campaign {locked!r} read for campaign {campaign_id!r}")
    return campaign_id
