"""Per-campaign evaluation lock (Architecture §10.1, §4.2).

Opening a campaign copies Group B (``research:``) plus derived values (template hash, holdout
range, …) into ``evaluation.lock.yaml``, makes it read-only, and records its SHA256 in the
ledger. Every run then calls :func:`assert_lock_matches`: a mid-campaign Group-B edit, or a
tampered lock file, refuses to run.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from quantcrucible.config.loader import research_to_dict
from quantcrucible.config.schema import UserConfig
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Campaign, utc_now


class LockMismatchError(RuntimeError):
    """``user.yaml`` Group B differs from the campaign lock — revert it or open a new campaign."""


class LockTamperedError(LockMismatchError):
    """The lock file no longer matches the hash recorded in the ledger."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)  # read-only attribute on Windows


class CampaignNotOpened(RuntimeError):
    """A new campaign cannot be opened yet (D4 unset, ADR-0019)."""


def _make_writable(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def read_lock(lock_path: Path) -> dict[str, Any]:
    with lock_path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict) or "research" not in data or "campaign_id" not in data:
        raise LockTamperedError(f"{lock_path} is not a valid evaluation lock")
    return data


def open_campaign(
    cfg: UserConfig,
    ledger: Ledger,
    campaign_id: str,
    lock_path: Path,
    holdout_range: str,
    holdout_lock_hash: str | None = None,
    derived: Mapping[str, Any] | None = None,
) -> Campaign:
    """Write the lock for a new campaign and register the campaign in the ledger.

    Refuses while the previous campaign's lock belongs to a campaign that is not BURNED or
    ABANDONED (ADR-0019); that lock is archived byte for byte under ``locks/``. Refuses a holdout
    that an earlier campaign already used — same manifest or an overlapping period (§4.2).
    """
    used = ledger.holdout_collision(holdout_range, holdout_lock_hash)
    if used is not None:
        raise LockMismatchError(
            f"holdout {holdout_range} was already used (claimed {used}); a used holdout joins "
            "the in-sample data and is never a holdout again (§4.2) — carve a new one"
        )
    if lock_path.exists():
        previous = read_lock(lock_path)
        prev_campaign = ledger.campaign(str(previous["campaign_id"]))
        if prev_campaign is not None and prev_campaign.status not in ("BURNED", "ABANDONED"):
            raise LockMismatchError(
                f"campaign {prev_campaign.campaign_id!r} is still {prev_campaign.status}; "
                "finish it (holdout opened ⇒ BURNED) or abandon it before opening a new one"
            )
        archive = lock_path.parent / "locks" / f"{previous['campaign_id']}.lock.yaml"
        archive.parent.mkdir(parents=True, exist_ok=True)
        _make_writable(lock_path)
        os.replace(lock_path, archive)
        _make_read_only(archive)

    content = {
        "campaign_id": campaign_id,
        "created_at": utc_now().isoformat(),
        "holdout_range": holdout_range,
        "holdout_lock_hash": holdout_lock_hash,
        "research": research_to_dict(cfg.research),
        "derived": dict(derived or {}),
    }
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        "# GENERATED when the campaign opened — never edit (Architecture §10.1).\n"
        + yaml.safe_dump(content, sort_keys=True, allow_unicode=True),
        encoding="utf-8",
        newline="\n",
    )
    _make_read_only(lock_path)
    return ledger.open_campaign(
        campaign_id, holdout_range, sha256_file(lock_path), holdout_lock_hash,
        purpose=cfg.research.campaign.purpose, trial_budget=cfg.research.campaign.trial_budget,
    )  # fmt: skip


def assert_lock_matches(
    cfg: UserConfig, lock_path: Path, ledger: Ledger, campaign_id: str
) -> dict[str, Any]:
    """Refuse to run unless the lock is intact and Group B is unchanged. Returns the lock."""
    campaign = ledger.campaign(campaign_id)
    if campaign is None:
        raise LockMismatchError(f"unknown campaign {campaign_id!r}")
    if not lock_path.exists():
        raise LockTamperedError(f"{lock_path} is missing")
    if sha256_file(lock_path) != campaign.lock_hash:
        raise LockTamperedError(f"{lock_path} does not match the hash recorded for {campaign_id}")
    lock = read_lock(lock_path)
    if lock["campaign_id"] != campaign_id:
        raise LockMismatchError(f"{lock_path} belongs to campaign {lock['campaign_id']!r}")
    current = research_to_dict(cfg.research)
    locked = lock["research"]
    if current != locked:
        changed = sorted(k for k in set(current) | set(locked) if current.get(k) != locked.get(k))
        raise LockMismatchError(
            f"research settings changed mid-campaign ({', '.join(changed)}). "
            "Revert config/user.yaml or open a new campaign."
        )
    return lock
