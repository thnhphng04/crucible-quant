"""Carve the holdout out of freshly downloaded data — once (Architecture §4.2 layer 2, P6).

Downloaded bars are held in memory only. The in-sample part goes to the research folder; the
holdout part goes to the holdout folder, is made read-only (plus a deny-write ACL on Windows,
ADR-0001), and its SHA256 manifest is written to ``holdout.lock``. A second carve is refused:
the holdout is write-once.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import stat
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np

from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.store import _day_start, file_name, write_bars


class HoldoutLockError(RuntimeError):
    """The holdout already exists, or its files no longer match ``holdout.lock``."""


@dataclass(frozen=True, slots=True)
class CarveSummary:
    holdout_range: str  # "start/end", end exclusive
    in_sample_rows: dict[str, int]
    holdout_files: int
    lock_sha256: str


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# Specific rights only. The generic "(W)" right includes SYNCHRONIZE / READ_CONTROL, and denying
# those makes the file unreadable too.
_DENY_WRITE_RIGHTS = "(WD,AD,WEA,WA,DE)"  # write data, append, write (ext.) attributes, delete


def make_read_only(path: Path, harden: bool = True) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
    if harden and sys.platform == "win32":
        subprocess.run(
            ["icacls", str(path), "/deny", f"{getpass.getuser()}:{_DENY_WRITE_RIGHTS}"],
            check=True,
            capture_output=True,
        )


def remove_deny_acl(path: Path) -> None:
    """Drop this user's deny entries (Windows) — used only to repair or re-apply hardening."""
    if sys.platform == "win32":
        subprocess.run(
            ["icacls", str(path), "/remove:d", getpass.getuser()],
            check=True,
            capture_output=True,
        )


def reharden(holdout_dir: Path, lock_path: Path) -> CarveSummary:
    """Re-apply read-only protection after verifying every file against ``holdout.lock``."""
    paths = [lock_path, *sorted(holdout_dir.iterdir())]
    for path in paths:
        remove_deny_acl(path)
    verify_holdout(lock_path, holdout_dir)
    manifest = read_holdout_lock(lock_path)
    for path in paths:
        make_read_only(path)
    return CarveSummary(manifest["range"], {}, len(manifest["files"]), sha256_file(lock_path))


def carve(
    bars_by_symbol: Mapping[str, Bars],
    holdout_start: date,
    holdout_end: date,
    in_sample_dir: Path,
    holdout_dir: Path,
    lock_path: Path,
    harden: bool = True,
) -> CarveSummary:
    """Split every series at ``holdout_start`` (by bar close time) and lock the holdout part."""
    if lock_path.exists():
        raise HoldoutLockError(f"{lock_path} exists — the holdout is write-once")
    if not holdout_start < holdout_end:
        raise ValueError("holdout_start must be before holdout_end")
    cut, stop = _day_start(holdout_start), _day_start(holdout_end)
    holdout_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}
    in_sample_rows: dict[str, int] = {}
    for symbol, bars in bars_by_symbol.items():
        is_idx = np.flatnonzero(bars.ts < cut)
        ho_idx = np.flatnonzero((bars.ts >= cut) & (bars.ts < stop))
        is_part = bars.slice(0, int(is_idx[-1]) + 1) if len(is_idx) else bars.slice(0, 0)
        ho_part = (
            bars.slice(int(ho_idx[0]), int(ho_idx[-1]) + 1) if len(ho_idx) else bars.slice(0, 0)
        )
        name = file_name(symbol, bars.timeframe)
        write_bars(in_sample_dir / name, is_part)
        in_sample_rows[symbol] = len(is_part)
        target = holdout_dir / name
        write_bars(target, ho_part)
        files[name] = sha256_file(target)
        make_read_only(target, harden)
    manifest: dict[str, Any] = {
        "range": f"{holdout_start.isoformat()}/{holdout_end.isoformat()}",
        "created_at": datetime.now(UTC).isoformat(),
        "files": dict(sorted(files.items())),
    }
    lock_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    make_read_only(lock_path, harden)
    return CarveSummary(manifest["range"], in_sample_rows, len(files), sha256_file(lock_path))


def read_holdout_lock(lock_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(lock_path.read_text(encoding="utf-8"))
    return data


def verify_holdout(lock_path: Path, holdout_dir: Path) -> None:
    """Raise unless every holdout file matches its SHA256 in ``holdout.lock``."""
    manifest = read_holdout_lock(lock_path)
    for name, digest in manifest["files"].items():
        path = holdout_dir / name
        if not path.exists() or sha256_file(path) != digest:
            raise HoldoutLockError(f"holdout file {name} is missing or was modified")
