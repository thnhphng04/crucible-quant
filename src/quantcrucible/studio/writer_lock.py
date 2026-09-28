"""One project-wide writer across Studio jobs and direct CLI commands."""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

if sys.platform == "win32":
    import msvcrt

    def _take(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)

    def _drop(fd: int) -> None:
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    def _take(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def _drop(fd: int) -> None:
        fcntl.flock(fd, fcntl.LOCK_UN)


class ProjectBusy(RuntimeError):
    pass


@contextmanager
def project_writer_lock(root: Path) -> Iterator[None]:
    path = root / "studio" / "writer.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        handle.seek(0)
        try:
            _take(handle.fileno())
        except OSError as exc:
            raise ProjectBusy("another campaign or data job is writing this project") from exc
        try:
            handle.seek(1)
            handle.truncate()
            handle.write(f"pid {os.getpid()}".encode())
            handle.flush()
            jobs_db = root / "studio" / "jobs.sqlite"
            if jobs_db.exists():
                with sqlite3.connect(jobs_db) as conn:
                    active = conn.execute(
                        """SELECT job_id FROM jobs WHERE state IN
                           ('starting','running','stopping','unknown') LIMIT 1"""
                    ).fetchone()
                if active is not None:
                    raise ProjectBusy(
                        f"Studio job {active[0]} may still be writing; reopen Studio to reconcile"
                    )
            yield
        finally:
            handle.seek(0)
            _drop(handle.fileno())
