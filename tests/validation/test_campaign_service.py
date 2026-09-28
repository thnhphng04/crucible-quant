"""Explicit campaign create service for Studio."""

from __future__ import annotations

import os
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from quantcrucible.config.lock import LockMismatchError, LockTamperedError, sha256_file
from quantcrucible.config.schema import UserConfig
from quantcrucible.ledger.db import Ledger, LedgerError
from quantcrucible.validation.campaign_service import (
    CampaignService,
    DatasetDescriptor,
    WriterLockBusy,
    preview_campaign,
    project_writer_lock,
)


def _cfg() -> UserConfig:
    return replace(UserConfig(), research=replace(UserConfig().research, holdout_pass=0.5))


def _dataset(suffix: str = "a") -> DatasetDescriptor:
    return DatasetDescriptor(
        dataset_id=f"dataset-{suffix}",
        manifest_sha256=f"manifest-{suffix}",
        holdout_range="2030-01-01/2031-01-01",
        holdout_lock_sha256=f"holdout-{suffix}",
        resolved_data_end="2029-12-31",
    )


def test_preview_renders_dataset_fields_without_writing(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")

    preview = preview_campaign(tmp_path, _cfg(), ledger, _dataset(), campaign_id="c-preview")

    assert preview.ok
    assert "dataset-a" in preview.text
    assert preview.lock_sha256
    assert not (tmp_path / "config").exists()
    assert ledger.campaigns() == []


def test_preview_and_create_require_d4(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    dataset = _dataset()

    preview = preview_campaign(tmp_path, UserConfig(), ledger, dataset, campaign_id="c-preview")

    assert not preview.ok
    assert any("holdout_pass" in problem for problem in preview.problems)
    with pytest.raises(LockMismatchError, match="holdout_pass"):
        CampaignService(tmp_path, ledger).create_campaign(
            UserConfig(), dataset, draft_id="draft-1", request_key="request-1", campaign_id="c1"
        )


def test_create_campaign_is_idempotent_and_resolvable(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    service = CampaignService(tmp_path, ledger)

    first = service.create_campaign(
        _cfg(), _dataset(), draft_id="draft-1", request_key="request-1", campaign_id="c-studio"
    )
    second = service.create_campaign(
        _cfg(), _dataset(), draft_id="draft-1", request_key="request-1", campaign_id="c-studio"
    )

    canonical = tmp_path / "config" / "locks" / "c-studio.lock.yaml"
    active = tmp_path / "config" / "evaluation.lock.yaml"
    assert first == second
    assert first.lock_path == canonical
    assert canonical.exists() and active.exists()
    assert sha256_file(canonical) == first.lock_sha256 == sha256_file(active)
    request = ledger.creation_request("request-1")
    assert request is not None
    assert request[1:] == ("c-studio", "dataset-a")
    resolved = service.resolve_campaign("c-studio")
    assert resolved.kind == "canonical"
    assert resolved.data["derived"]["dataset_id"] == "dataset-a"


def test_create_campaign_rejects_reused_key_with_different_payload(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    service = CampaignService(tmp_path, ledger)
    service.create_campaign(
        _cfg(), _dataset("a"), draft_id="draft-1", request_key="request-1", campaign_id="c1"
    )

    with pytest.raises(LedgerError, match="another payload"):
        service.create_campaign(
            _cfg(), _dataset("b"), draft_id="draft-1", request_key="request-1", campaign_id="c1"
        )


def test_create_campaign_restores_missing_active_copy_on_retry(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    service = CampaignService(tmp_path, ledger)
    created = service.create_campaign(
        _cfg(), _dataset(), draft_id="draft-1", request_key="request-1", campaign_id="c-studio"
    )
    active = tmp_path / "config" / "evaluation.lock.yaml"
    os.chmod(active, stat.S_IREAD | stat.S_IWRITE)
    active.unlink()

    again = service.create_campaign(
        _cfg(), _dataset(), draft_id="draft-1", request_key="request-1", campaign_id="c-studio"
    )

    assert again == created
    assert active.exists()
    assert sha256_file(active) == created.lock_sha256


def test_startup_recovers_committed_studio_campaign_active_copy(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    service = CampaignService(tmp_path, ledger)
    created = service.create_campaign(
        _cfg(), _dataset(), draft_id="draft-1", request_key="request-1", campaign_id="c-studio"
    )
    active = tmp_path / "config" / "evaluation.lock.yaml"
    os.chmod(active, stat.S_IREAD | stat.S_IWRITE)
    active.unlink()

    assert service.recover_latest_active_copy() == "c-studio"
    assert sha256_file(active) == created.lock_sha256
    assert service.recover_latest_active_copy() is None


def test_create_campaign_refuses_open_active_campaign(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    service = CampaignService(tmp_path, ledger)
    service.create_campaign(
        _cfg(), _dataset("a"), draft_id="draft-1", request_key="request-1", campaign_id="c1"
    )

    with pytest.raises(LockMismatchError, match="still OPEN"):
        service.create_campaign(
            _cfg(), _dataset("b"), draft_id="draft-2", request_key="request-2", campaign_id="c2"
        )


def test_retry_refuses_tampered_canonical_lock(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    service = CampaignService(tmp_path, ledger)
    service.create_campaign(
        _cfg(), _dataset(), draft_id="draft-1", request_key="request-1", campaign_id="c-studio"
    )
    canonical = tmp_path / "config" / "locks" / "c-studio.lock.yaml"
    os.chmod(canonical, stat.S_IREAD | stat.S_IWRITE)
    canonical.write_text(
        canonical.read_text(encoding="utf-8").replace("dataset-a", "evil"), "utf-8"
    )

    with pytest.raises(LockTamperedError):
        service.create_campaign(
            _cfg(), _dataset(), draft_id="draft-1", request_key="request-1", campaign_id="c-studio"
        )


def test_create_uses_shared_nonblocking_studio_writer_lock(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    service = CampaignService(tmp_path, ledger)

    with (
        project_writer_lock(tmp_path),
        pytest.raises(WriterLockBusy, match=r"studio.*writer\.lock"),
    ):
        service.create_campaign(
            _cfg(), _dataset(), draft_id="draft-1", request_key="request-1", campaign_id="c1"
        )
