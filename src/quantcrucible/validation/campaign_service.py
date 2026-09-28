"""Explicit campaign creation and resolution services for Studio/API adapters.

This module deliberately takes a campaign id or a dataset descriptor from its caller. It never
discovers a campaign by looking at the active lock and never creates one as a side effect of Run.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from quantcrucible.config.lock import (
    CampaignNotOpened,
    DryRun,
    LockMismatchError,
    LockTamperedError,
    ResolvedCampaignLock,
    _make_read_only,
    _make_writable,
    lock_text,
    resolve_campaign,
    sha256_file,
)
from quantcrucible.config.schema import UserConfig
from quantcrucible.ledger.db import Ledger, LedgerError
from quantcrucible.ledger.records import Campaign, utc_now
from quantcrucible.validation.run import _check_trial_budget


@dataclass(frozen=True, slots=True)
class DatasetDescriptor:
    """The immutable dataset input Studio prepared before a campaign can be created."""

    dataset_id: str
    manifest_sha256: str
    holdout_range: str
    holdout_lock_sha256: str | None
    resolved_data_end: str
    extra_derived: Mapping[str, Any] | None = None

    def derived(self) -> dict[str, Any]:
        values = dict(self.extra_derived or {})
        values.update(
            {
                "dataset_id": self.dataset_id,
                "manifest_sha256": self.manifest_sha256,
                "holdout_lock_sha256": self.holdout_lock_sha256,
                "resolved_data_end": self.resolved_data_end,
            }
        )
        return values


@dataclass(frozen=True, slots=True)
class CampaignPreview:
    text: str
    lock_sha256: str
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


@dataclass(frozen=True, slots=True)
class CreatedCampaign:
    campaign: Campaign
    lock_path: Path
    lock_sha256: str
    dataset_id: str


class WriterLockBusy(RuntimeError):
    """Another project writer is active."""


def draft_digest(draft_id: str, cfg: UserConfig, dataset: DatasetDescriptor) -> str:
    payload = {
        "draft_id": draft_id,
        "research": yaml.safe_load(
            lock_text(
                cfg,
                "preview",
                dataset.holdout_range,
                dataset.holdout_lock_sha256,
                dataset.derived(),
                "1970-01-01T00:00:00+00:00",
            )
        )["research"],
        "dataset": {
            "dataset_id": dataset.dataset_id,
            "manifest_sha256": dataset.manifest_sha256,
            "holdout_range": dataset.holdout_range,
            "holdout_lock_sha256": dataset.holdout_lock_sha256,
            "resolved_data_end": dataset.resolved_data_end,
        },
    }
    text = yaml.safe_dump(payload, sort_keys=True)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def preview_campaign(
    root: Path,
    cfg: UserConfig,
    ledger: Ledger,
    dataset: DatasetDescriptor,
    *,
    campaign_id: str = "preview",
    derived: Mapping[str, Any] | None = None,
) -> CampaignPreview:
    """Run the create admission checks and render the exact lock text without writing."""
    merged_derived = dataset.derived()
    merged_derived.update(dict(derived or {}))
    dry = _dry_run_canonical(
        cfg,
        ledger,
        campaign_id,
        root / "config" / "evaluation.lock.yaml",
        dataset.holdout_range,
        dataset.holdout_lock_sha256,
        merged_derived,
    )
    digest = hashlib.sha256(dry.text.encode("utf-8")).hexdigest()
    return CampaignPreview(dry.text, digest, dry.problems)


class CampaignService:
    def __init__(self, root: Path, ledger: Ledger) -> None:
        self.root = root
        self.ledger = ledger
        self.config_dir = root / "config"
        self.active_lock = self.config_dir / "evaluation.lock.yaml"
        self.locks_dir = self.config_dir / "locks"

    def resolve_campaign(self, campaign_id: str) -> ResolvedCampaignLock:
        return resolve_campaign(campaign_id, self.ledger, self.active_lock)

    def recover_latest_active_copy(self) -> str | None:
        """Finish a Studio Create that committed to the ledger before its active copies."""
        campaigns = self.ledger.campaigns()
        if not campaigns:
            return None
        newest = campaigns[-1]
        request = self.ledger._conn.execute(  # same package owns the recovery transaction
            "SELECT 1 FROM campaign_creation_requests WHERE campaign_id=?",
            (newest.campaign_id,),
        ).fetchone()
        if request is None:
            return None
        resolved = self.resolve_campaign(newest.campaign_id)
        active_id: str | None = None
        if self.active_lock.exists():
            active = yaml.safe_load(self.active_lock.read_text(encoding="utf-8"))
            if isinstance(active, dict):
                active_id = str(active.get("campaign_id"))
            if active_id == newest.campaign_id:
                if sha256_file(self.active_lock) != newest.lock_hash:
                    raise LockTamperedError("active Studio lock differs from its ledger hash")
                return None
        with project_writer_lock(self.root):
            self._ensure_active_copy(resolved.path)
            path = self.config_dir / "user.yaml"
            existing = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
            operational = existing.get("operational", {}) if isinstance(existing, dict) else {}
            self._replace_user_yaml(
                yaml.safe_dump(
                    {"operational": operational, "research": resolved.data["research"]},
                    sort_keys=False,
                    allow_unicode=True,
                )
            )
        return newest.campaign_id

    def create_campaign(
        self,
        cfg: UserConfig,
        dataset: DatasetDescriptor,
        *,
        draft_id: str,
        request_key: str,
        campaign_id: str | None = None,
        derived: Mapping[str, Any] | None = None,
        user_yaml: str | None = None,
    ) -> CreatedCampaign:
        """Create a campaign explicitly and idempotently.

        ``user_yaml`` is optional so API tests can exercise the lock/ledger contract before the
        Studio draft store owns YAML rendering. When provided, it is atomically installed as
        ``config/user.yaml`` after the ledger row exists.
        """
        if not request_key.strip():
            raise ValueError("request_key must not be empty")
        campaign_id = campaign_id or _campaign_id_from_key(request_key)
        digest = draft_digest(draft_id, cfg, dataset)
        canonical = self.locks_dir / f"{campaign_id}.lock.yaml"
        merged_derived = dataset.derived()
        merged_derived.update(dict(derived or {}))
        created_at = utc_now().isoformat()
        text = lock_text(
            cfg, campaign_id, dataset.holdout_range, dataset.holdout_lock_sha256,
            merged_derived, created_at,
        )  # fmt: skip
        lock_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()

        with project_writer_lock(self.root):
            self._recover_request(request_key, digest, campaign_id, dataset, canonical)
            existing = self.ledger.creation_request(request_key)
            if existing is not None:
                old_digest, old_campaign_id, old_dataset_id = existing
                if existing != (digest, campaign_id, dataset.dataset_id):
                    raise LedgerError("creation request key already belongs to another payload")
                resolved = self.resolve_campaign(old_campaign_id)
                if old_digest != digest or old_dataset_id != dataset.dataset_id:
                    raise LedgerError("creation request metadata differs from this payload")
                self._ensure_active_copy(resolved.path)
                return CreatedCampaign(
                    resolved.campaign, resolved.path, resolved.campaign.lock_hash, old_dataset_id
                )

            preview = _dry_run_canonical(
                cfg, self.ledger, campaign_id, self.active_lock,
                dataset.holdout_range, dataset.holdout_lock_sha256, merged_derived,
            )  # fmt: skip
            if preview.problems:
                raise LockMismatchError("; ".join(preview.problems))

            self._write_canonical_lock(canonical, text, lock_hash)
            campaign = self.ledger.open_campaign_with_creation_request(
                request_key=request_key,
                draft_digest=digest,
                dataset_id=dataset.dataset_id,
                campaign_id=campaign_id,
                holdout_range=dataset.holdout_range,
                lock_hash=lock_hash,
                holdout_lock_hash=dataset.holdout_lock_sha256,
                purpose=cfg.research.campaign.purpose,
                trial_budget=cfg.research.campaign.trial_budget,
            )
            self._ensure_active_copy(canonical)
            if user_yaml is not None:
                self._replace_user_yaml(user_yaml)
            return CreatedCampaign(campaign, canonical, lock_hash, dataset.dataset_id)

    def _recover_request(
        self,
        request_key: str,
        digest: str,
        campaign_id: str,
        dataset: DatasetDescriptor,
        canonical: Path,
    ) -> None:
        existing = self.ledger.creation_request(request_key)
        if existing is None:
            return
        if existing != (digest, campaign_id, dataset.dataset_id):
            raise LedgerError("creation request key already belongs to another payload")
        resolved = self.resolve_campaign(campaign_id)
        if (
            resolved.path != canonical
            and canonical.exists()
            and sha256_file(canonical) != resolved.campaign.lock_hash
        ):
            raise LockTamperedError(f"{canonical} differs from the ledger hash")

    def _write_canonical_lock(self, canonical: Path, text: str, expected_hash: str) -> None:
        if canonical.exists():
            if sha256_file(canonical) != expected_hash:
                raise LockTamperedError(f"{canonical} already exists with different bytes")
            return
        pending = canonical.parent / ".pending" / canonical.name
        pending.parent.mkdir(parents=True, exist_ok=True)
        canonical.parent.mkdir(parents=True, exist_ok=True)
        _write_fsynced(pending, text)
        if sha256_file(pending) != expected_hash:
            raise LockTamperedError("pending lock bytes do not match expected hash")
        os.replace(pending, canonical)
        _make_read_only(canonical)

    def _ensure_active_copy(self, canonical: Path) -> None:
        if self.active_lock.exists() and sha256_file(self.active_lock) == sha256_file(canonical):
            return
        if self.active_lock.exists():
            previous = yaml.safe_load(self.active_lock.read_text(encoding="utf-8"))
            if isinstance(previous, dict) and previous.get("campaign_id"):
                archive = self.locks_dir / f"{previous['campaign_id']}.lock.yaml"
                archive.parent.mkdir(parents=True, exist_ok=True)
                if not archive.exists():
                    _make_writable(self.active_lock)
                    os.replace(self.active_lock, archive)
                    _make_read_only(archive)
                else:
                    if sha256_file(archive) != sha256_file(self.active_lock):
                        raise LockTamperedError(
                            f"archived lock {archive} differs from the active compatibility copy"
                        )
                    _make_writable(self.active_lock)
                    self.active_lock.unlink()
            else:
                _make_writable(self.active_lock)
                self.active_lock.unlink()
        pending = self.active_lock.with_name("evaluation.lock.pending.yaml")
        _write_fsynced(pending, canonical.read_text(encoding="utf-8"))
        os.replace(pending, self.active_lock)
        _make_read_only(self.active_lock)

    def _replace_user_yaml(self, user_yaml: str) -> None:
        path = self.config_dir / "user.yaml"
        archive = self.config_dir / "user.yaml.before-campaign"
        if path.exists():
            archive.write_bytes(path.read_bytes())
        pending = self.config_dir / "user.pending.yaml"
        _write_fsynced(pending, user_yaml)
        os.replace(pending, path)


def _dry_run_canonical(
    cfg: UserConfig,
    ledger: Ledger,
    campaign_id: str,
    active_lock: Path,
    holdout_range: str,
    holdout_lock_hash: str | None,
    derived: Mapping[str, Any],
) -> DryRun:
    problems: list[str] = []
    if cfg.research.holdout_pass is None:
        problems.append("research.holdout_pass (D4) is not set: it is locked with the campaign")
    try:
        _check_trial_budget(cfg, ledger, holdout_range)
    except CampaignNotOpened as e:
        problems.append(str(e))
    used = ledger.holdout_collision(holdout_range, holdout_lock_hash)
    if used is not None:
        problems.append(f"holdout {holdout_range} was already used (claimed {used})")
    if active_lock.exists():
        active = yaml.safe_load(active_lock.read_text(encoding="utf-8"))
        if isinstance(active, dict) and active.get("campaign_id") != campaign_id:
            prev = ledger.campaign(str(active["campaign_id"]))
            if prev is not None and prev.status not in ("BURNED", "ABANDONED"):
                problems.append(f"campaign {prev.campaign_id!r} is still {prev.status}")
    return DryRun(
        lock_text(cfg, campaign_id, holdout_range, holdout_lock_hash, derived,
                  utc_now().isoformat()),
        tuple(problems),
    )  # fmt: skip


def _campaign_id_from_key(request_key: str) -> str:
    return "c-" + hashlib.sha256(request_key.encode("utf-8")).hexdigest()[:16]


def _write_fsynced(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())


@contextmanager
def project_writer_lock(root: Path) -> Iterator[None]:
    lock_dir = root / "studio"
    lock_dir.mkdir(parents=True, exist_ok=True)
    path = lock_dir / "writer.lock"
    with path.open("a+b") as fh:
        try:
            _lock_file(fh)
        except OSError as e:
            raise WriterLockBusy(f"another project writer holds {path}") from e
        try:
            jobs_db = root / "studio" / "jobs.sqlite"
            if jobs_db.exists():
                with sqlite3.connect(jobs_db) as conn:
                    active = conn.execute(
                        """SELECT job_id FROM jobs WHERE state IN
                           ('starting','running','stopping','unknown') LIMIT 1"""
                    ).fetchone()
                if active is not None:
                    raise WriterLockBusy(
                        f"Studio job {active[0]} may still be writing; reconcile it before Create"
                    )
            yield
        finally:
            _unlock_file(fh)


if sys.platform == "win32":
    import msvcrt

    def _lock_file(fh: Any) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)

    def _unlock_file(fh: Any) -> None:
        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _lock_file(fh: Any) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _unlock_file(fh: Any) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
