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
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

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


ResolvedLockKind = Literal["active", "canonical", "legacy"]


@dataclass(frozen=True, slots=True)
class ResolvedCampaignLock:
    """A campaign lock resolved by explicit ID and verified against the ledger hash."""

    campaign: Campaign
    path: Path
    data: dict[str, Any]
    kind: ResolvedLockKind

    @property
    def sha256(self) -> str:
        return self.campaign.lock_hash


def _lock_is_legacy(data: Mapping[str, Any]) -> bool:
    derived = data.get("derived", {})
    return not isinstance(derived, Mapping) or "dataset_id" not in derived


def _verified_lock(
    path: Path, ledger_hash: str, campaign_id: str, kind: ResolvedLockKind
) -> ResolvedCampaignLock | None:
    if not path.exists():
        return None
    if sha256_file(path) != ledger_hash:
        raise LockTamperedError(f"{path} does not match the hash recorded for {campaign_id}")
    data = read_lock(path)
    if data["campaign_id"] != campaign_id:
        raise LockMismatchError(f"{path} belongs to campaign {data['campaign_id']!r}")
    # A canonical path can still hold a pre-dataset lock archived there by old code.
    resolved_kind = "legacy" if _lock_is_legacy(data) else kind
    return ResolvedCampaignLock(
        campaign=Campaign(campaign_id, utc_now(), "", ledger_hash, None, "OPEN"),
        path=path,
        data=data,
        kind=resolved_kind,
    )


def resolve_campaign(
    campaign_id: str, ledger: Ledger, active_lock_path: Path
) -> ResolvedCampaignLock:
    """Resolve exactly ``campaign_id`` without falling through to another active campaign.

    New campaigns live at ``config/locks/<campaign_id>.lock.yaml``. Older ledgers may only have
    the active compatibility copy (``evaluation.lock.yaml``), or an archive written by the former
    active-lock flow. Every candidate path is accepted only when both the file's campaign id and
    SHA256 match the ledger row.
    """
    campaign = ledger.campaign(campaign_id)
    if campaign is None:
        raise LockMismatchError(f"unknown campaign {campaign_id!r}")
    canonical = active_lock_path.parent / "locks" / f"{campaign_id}.lock.yaml"
    candidates: tuple[tuple[Path, ResolvedLockKind], ...] = (
        (canonical, "canonical"),
        (active_lock_path, "active"),
    )
    for path, kind in candidates:
        resolved = _verified_lock(path, campaign.lock_hash, campaign_id, kind)
        if resolved is not None:
            return ResolvedCampaignLock(campaign, resolved.path, resolved.data, resolved.kind)
    raise LockTamperedError(f"lock for campaign {campaign_id!r} is missing")


LOCK_HEADER = "# GENERATED when the campaign opened — never edit (Architecture §10.1).\n"


def lock_text(
    cfg: UserConfig,
    campaign_id: str,
    holdout_range: str,
    holdout_lock_hash: str | None,
    derived: Mapping[str, Any] | None,
    created_at: str,
) -> str:
    """Render a lock's exact bytes. The single source of the document, so a preview and the real
    write cannot drift apart (P3-24)."""
    content = {
        "campaign_id": campaign_id,
        "created_at": created_at,
        "holdout_range": holdout_range,
        "holdout_lock_hash": holdout_lock_hash,
        "research": research_to_dict(cfg.research),
        "derived": dict(derived or {}),
    }
    return LOCK_HEADER + yaml.safe_dump(content, sort_keys=True, allow_unicode=True)


@dataclass(frozen=True, slots=True)
class DryRun:
    """What opening a campaign *would* do. ``problems`` is empty when it would succeed."""

    text: str
    problems: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.problems


def dry_run_lock(
    cfg: UserConfig,
    ledger: Ledger,
    campaign_id: str,
    lock_path: Path,
    holdout_range: str,
    holdout_lock_hash: str | None = None,
    derived: Mapping[str, Any] | None = None,
) -> DryRun:
    """Answer "would this open?" without writing, archiving or registering anything.

    Opening a campaign is irreversible three ways at once — a read-only lock, the previous lock
    archived, and an append-only ledger row — so the question could previously only be answered by
    doing it. Before the first perpetual campaign that answer depends on data just downloaded and a
    holdout just carved, and learning it by opening a campaign means burning one.

    It **reports** rather than raises: a dry run answers, and the caller decides. The real
    :func:`open_campaign` keeps raising, so nothing became permissive.
    """
    problems: list[str] = []
    used = ledger.holdout_collision(holdout_range, holdout_lock_hash)
    if used is not None:
        problems.append(
            f"holdout {holdout_range} was already used (claimed {used}); a used holdout joins the "
            "in-sample data and is never a holdout again (§4.2) — carve a new one"
        )
    if lock_path.exists():
        previous = read_lock(lock_path)
        prev = ledger.campaign(str(previous["campaign_id"]))
        if prev is not None and prev.status not in ("BURNED", "ABANDONED"):
            problems.append(
                f"campaign {prev.campaign_id!r} is still {prev.status}; finish it (holdout "
                "opened ⇒ BURNED) or abandon it before opening a new one"
            )
    return DryRun(
        text=lock_text(
            cfg, campaign_id, holdout_range, holdout_lock_hash, derived, utc_now().isoformat()
        ),
        problems=tuple(problems),
    )


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

    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        lock_text(
            cfg, campaign_id, holdout_range, holdout_lock_hash, derived, utc_now().isoformat()
        ),
        encoding="utf-8",
        newline="\n",
    )
    _make_read_only(lock_path)
    return ledger.open_campaign(
        campaign_id, holdout_range, sha256_file(lock_path), holdout_lock_hash,
        purpose=cfg.research.campaign.purpose, trial_budget=cfg.research.campaign.trial_budget,
    )  # fmt: skip


# Group B keys added after campaigns were already locked, with the value a lock written before
# the key existed implies (ADR-0039). Such a lock matches only a config at that value; a new lock
# always writes the key.
LOCK_ADDITIONS: dict[str, Any] = {"backtest": {"precision": "float64"}}


def _comparable(current: dict[str, Any], locked: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(current)
    for key, implied in LOCK_ADDITIONS.items():
        if key not in locked and out.get(key) == implied:
            del out[key]
    return out


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
    locked = lock["research"]
    current = _comparable(research_to_dict(cfg.research), locked)
    if current != locked:
        changed = sorted(k for k in set(current) | set(locked) if current.get(k) != locked.get(k))
        raise LockMismatchError(
            f"research settings changed mid-campaign ({', '.join(changed)}). "
            "Revert config/user.yaml or open a new campaign."
        )
    return lock
