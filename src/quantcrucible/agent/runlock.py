"""One evolve run per campaign at a time (INV-72).

The scheduler holds its counts in memory, so it can only keep the trial budget of two workers
of the *same* run apart. Two ``evolve`` processes each read the ledger, each believe the quota
is free and together overshoot ``N`` — which never resets. A lock taken for the whole run keeps
them apart.

The lock is an OS file lock, not a row: the operating system drops it when the process dies, so
a crashed run leaves nothing to clean up by hand. It carries no state — the ledger stays the
only state (ADR-0024) — just the holder's pid, so the refusal can name it.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

if sys.platform == "win32":
    import msvcrt

    def _take(fh: object) -> None:
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]

    def _drop(fh: object) -> None:
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]

else:
    import fcntl

    def _take(fh: object) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]

    def _drop(fh: object) -> None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


class RunLockError(RuntimeError):
    """Another run holds this campaign's lock."""


def lock_path(ledger_path: Path | str, campaign_id: str) -> Path:
    return Path(ledger_path).parent / f"{campaign_id}.runlock"


def _holder(path: Path) -> str:
    try:
        return path.read_bytes()[1:].decode(errors="replace").strip() or "an unknown process"
    except OSError:  # pragma: no cover - unreadable lock file
        return "an unknown process"


@contextmanager
def campaign_run_lock(ledger_path: Path | str, campaign_id: str, run_label: str) -> Iterator[Path]:
    """Hold the campaign's run lock for the block, or refuse."""
    path = lock_path(ledger_path, campaign_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as fh:
        fh.seek(0)
        try:
            _take(fh)
        except OSError as e:
            raise RunLockError(
                f"campaign {campaign_id} is already being evolved by {_holder(path)}: "
                "two runs would share the trial budget without seeing each other"
            ) from e
        try:
            fh.seek(1)
            fh.truncate()
            fh.write(f"pid {os.getpid()} ({run_label})".encode())
            fh.flush()
            yield path
        finally:
            fh.seek(0)
            _drop(fh)
