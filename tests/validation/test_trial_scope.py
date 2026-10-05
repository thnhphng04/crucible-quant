"""The locked trial scope (P3-63, ADR-0050) — INV-125."""

from __future__ import annotations

import pytest

from quantcrucible.validation.trial_scope import CAMPAIGN_V1, trial_scope


def test_a_lock_without_the_key_counts_the_whole_ledger() -> None:
    assert trial_scope({"derived": {}}, "c1") is None
    assert trial_scope({}, "c1") is None


def test_a_tagged_lock_counts_its_campaign() -> None:
    lock = {"campaign_id": "c1", "derived": {"trial_scope": CAMPAIGN_V1}}
    assert trial_scope(lock, "c1") == "c1"
    assert trial_scope({"derived": {"trial_scope": CAMPAIGN_V1}}, "c7") == "c7"


def test_an_unknown_tag_or_another_campaigns_lock_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown locked trial scope"):
        trial_scope({"derived": {"trial_scope": "campaign_v2"}}, "c1")
    with pytest.raises(ValueError, match="read for campaign 'c2'"):
        trial_scope({"campaign_id": "c1", "derived": {"trial_scope": CAMPAIGN_V1}}, "c2")
