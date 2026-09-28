"""Durable local job supervision for Studio write operations."""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any, Literal, Self, cast

type JobKind = Literal["prepare_data", "evolve", "portfolio"]
type JobState = Literal[
    "queued",
    "starting",
    "running",
    "stopping",
    "succeeded",
    "failed",
    "interrupted",
    "unknown",
]

ACTIVE_STATES: frozenset[JobState] = frozenset({"starting", "running", "stopping", "unknown"})
FINAL_STATES: frozenset[JobState] = frozenset({"succeeded", "failed", "interrupted"})
KINDS: frozenset[JobKind] = frozenset({"prepare_data", "evolve", "portfolio"})
COMMAND_VERSION = "studio-jobs-v1"


class JobError(RuntimeError):
    """Base class for Studio job failures."""


class JobConflict(JobError):
    """The requested job operation conflicts with durable state or a writer lock."""


class JobNotFound(JobError):
    """The requested job id is not present in the Studio job store."""


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    kind: JobKind
    state: JobState
    request_key: str
    campaign_id: str | None
    draft_id: str | None
    pid: int | None
    process_identity: str | None
    worker_nonce: str
    command_version: str
    argv: tuple[str, ...]
    log_path: Path
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    heartbeat_at: datetime | None = None
    exit_code: int | None = None
    error_code: str | None = None
    error_message: str | None = None


@dataclass
class _RunningProcess:
    process: subprocess.Popen[bytes]


class _HeldWriterLock:
    def __init__(self, path: Path, fh: Any) -> None:
        self.path = path
        self._fh = fh
        self._closed = False

    def close(self) -> None:
        if self._closed:
            return
        try:
            _unlock_file(self._fh)
        finally:
            self._fh.close()
            self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


class JobSupervisor:
    """SQLite-backed supervisor for one local Studio project root.

    The caller supplies command argument arrays from server-owned routing code. This class never
    accepts shell text and always launches with ``shell=False``.
    """

    def __init__(
        self,
        project_root: Path | str,
        *,
        writer_lock_path: Path | str | None = None,
        postconditions: Mapping[JobKind, Callable[[JobRecord], bool]] | None = None,
        stop_timeout: float = 10.0,
    ) -> None:
        self.project_root = Path(project_root)
        self.studio_dir = self.project_root / "studio"
        self.db_path = self.studio_dir / "jobs.sqlite"
        self.log_dir = self.studio_dir / "logs"
        self.worker_dir = self.studio_dir / "workers"
        self.writer_lock_path = (
            Path(writer_lock_path)
            if writer_lock_path is not None
            else self.studio_dir / "writer.lock"
        )
        self.stop_timeout = stop_timeout
        self._postconditions = dict(postconditions or {})
        self._running: dict[str, _RunningProcess] = {}
        self._running_lock = threading.Lock()
        self._init_db()

    def enqueue(
        self,
        *,
        kind: JobKind,
        argv: Iterable[str],
        request_key: str,
        campaign_id: str | None = None,
        draft_id: str | None = None,
        command_version: str = COMMAND_VERSION,
    ) -> JobRecord:
        self._check_kind(kind)
        checked_argv = self._check_argv(argv)
        if not request_key:
            raise ValueError("request_key is required")
        if campaign_id is None and draft_id is None:
            raise ValueError("campaign_id or draft_id is required")

        payload_digest = self._request_digest(
            kind=kind,
            argv=checked_argv,
            campaign_id=campaign_id,
            draft_id=draft_id,
            command_version=command_version,
        )
        now = _utc_now()
        job_id = f"job-{secrets.token_hex(12)}"
        nonce = secrets.token_hex(16)
        log_path = self.log_dir / f"{job_id}.log"
        with self._connect() as conn:
            row = conn.execute(
                "SELECT job_id, request_digest FROM jobs WHERE request_key = ?", (request_key,)
            ).fetchone()
            if row is not None:
                if row["request_digest"] != payload_digest:
                    raise JobConflict("request key was already used with a different payload")
                return self.status(str(row["job_id"]))
            conn.execute(
                """
                INSERT INTO jobs (
                    job_id, kind, campaign_id, draft_id, request_key, request_digest, state,
                    worker_nonce, command_version, argv_json, log_path, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job_id,
                    kind,
                    campaign_id,
                    draft_id,
                    request_key,
                    payload_digest,
                    nonce,
                    command_version,
                    json.dumps(list(checked_argv), separators=(",", ":")),
                    _to_db_path(log_path),
                    _ts(now),
                    _ts(now),
                ),
            )
        return self.status(job_id)

    def start(self, job_id: str, *, extra_env: Mapping[str, str] | None = None) -> JobRecord:
        with self._running_lock:
            if job_id in self._running:
                return self.status(job_id)

            with self._connect() as conn:
                job = self._row_to_record(self._job_row(conn, job_id))
                if job.state == "running":
                    return job
                if job.state != "queued":
                    raise JobConflict(f"job {job_id} is {job.state}, not queued")
                active = conn.execute(
                    "SELECT job_id, state FROM jobs"
                    " WHERE state IN ('starting','running','stopping','unknown')"
                    " AND job_id <> ? ORDER BY updated_at DESC LIMIT 1",
                    (job_id,),
                ).fetchone()
                if active is not None:
                    raise JobConflict(
                        f"job {active['job_id']} is {active['state']}; only one writer may run"
                    )
                lock = self._try_writer_lock()
                lock.close()
                now = _utc_now()
                conn.execute(
                    "UPDATE jobs SET state = 'starting', updated_at = ? WHERE job_id = ?",
                    (_ts(now), job_id),
                )

            try:
                env = dict(os.environ)
                self._add_src_to_env(env)
                payload_path = self.worker_dir / f"{job.job_id}.json"
                payload_env = {
                    "QUANTCRUCIBLE_STUDIO_JOB_ID": job.job_id,
                    "QUANTCRUCIBLE_STUDIO_WORKER_NONCE": job.worker_nonce,
                    "QUANTCRUCIBLE_STUDIO_HEARTBEAT": _to_db_path(
                        self.studio_dir / f"{job.job_id}.heartbeat"
                    ),
                }
                payload = {
                    "argv": list(job.argv),
                    "cwd": str(self.project_root),
                    "env": payload_env,
                    "lock_path": str(self.writer_lock_path),
                    "log_path": str(job.log_path),
                    "stop_timeout": self.stop_timeout,
                    "stop_path": str(self.worker_dir / f"{job.job_id}.stop"),
                }
                env.update(
                    {
                        "QUANTCRUCIBLE_STUDIO_JOB_ID": job.job_id,
                        "QUANTCRUCIBLE_STUDIO_WORKER_NONCE": job.worker_nonce,
                    }
                )
                if extra_env is not None:
                    payload_env.update(extra_env)
                payload_path.write_text(
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                    encoding="utf-8",
                )
                process = subprocess.Popen(
                    (
                        sys.executable,
                        "-m",
                        "quantcrucible.studio.jobs",
                        "--worker",
                        str(payload_path),
                    ),
                    cwd=self.project_root,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    stdin=subprocess.DEVNULL,
                    shell=False,
                    env=env,
                )
                self._wait_for_worker_lock(process)
            except BaseException as exc:
                self._mark_failed_start(job_id, exc)
                raise

            identity = _process_identity(process.pid)
            now = _utc_now()
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE jobs
                       SET state = 'running', pid = ?, process_identity = ?, started_at = ?,
                           heartbeat_at = ?, updated_at = ?
                     WHERE job_id = ?
                    """,
                    (_pid_to_db(process.pid), identity, _ts(now), _ts(now), _ts(now), job_id),
                )
            self._running[job_id] = _RunningProcess(process)
            threading.Thread(target=self._monitor, args=(job_id,), daemon=True).start()
            return self.status(job_id)

    def stop(self, job_id: str, *, request_key: str | None = None) -> JobRecord:
        del request_key  # Stop is idempotent by job id; API layers may still require a key.
        job = self.status(job_id)
        if job.state in FINAL_STATES:
            return job
        if job.state == "queued":
            now = _utc_now()
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE jobs
                       SET state = 'interrupted', error_code = 'STOPPED_BEFORE_START',
                           updated_at = ?, finished_at = ?
                     WHERE job_id = ?
                    """,
                    (_ts(now), _ts(now), job_id),
                )
            return self.status(job_id)
        with self._running_lock:
            locally_owned = job_id in self._running
        if not locally_owned and (
            job.pid is None or not _is_same_process(job.pid, job.process_identity)
        ):
            if self._writer_lock_is_held():
                raise JobConflict(
                    "worker identity cannot be verified while a writer holds the lock"
                )
            now = _utc_now()
            with self._connect() as conn:
                conn.execute(
                    """UPDATE jobs SET state = 'interrupted', error_code = 'STALE_PROCESS',
                       updated_at = ?, finished_at = ? WHERE job_id = ?""",
                    (_ts(now), _ts(now), job_id),
                )
            return self.status(job_id)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET state = 'stopping', updated_at = ? WHERE job_id = ?",
                (_ts(now), job_id),
            )
        (self.worker_dir / f"{job_id}.stop").write_text("stop\n", encoding="ascii")
        self._terminate(job_id, job.pid, job.process_identity)
        if not locally_owned:
            threading.Thread(
                target=self._monitor_detached_stop,
                args=(job_id, job.pid, job.process_identity),
                daemon=True,
            ).start()
        return self.status(job_id)

    def status(self, job_id: str) -> JobRecord:
        with self._connect() as conn:
            return self._row_to_record(self._job_row(conn, job_id))

    def list_jobs(self, *, limit: int = 50) -> list[JobRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def reconcile_startup(self) -> list[JobRecord]:
        reconciled: list[JobRecord] = []
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE state IN ('starting','running','stopping')"
            ).fetchall()
        for row in rows:
            job = self._row_to_record(row)
            next_state = self._reconcile_state(job)
            if next_state is None:
                reconciled.append(job)
                continue
            now = _utc_now()
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE jobs
                       SET state = ?, updated_at = ?, finished_at = COALESCE(finished_at, ?),
                           error_code = COALESCE(error_code, ?)
                     WHERE job_id = ?
                    """,
                    (
                        next_state,
                        _ts(now),
                        _ts(now),
                        "RECONCILED_UNKNOWN" if next_state == "unknown" else "RECONCILED_DEAD",
                        job.job_id,
                    ),
                )
            reconciled.append(self.status(job.job_id))
        return reconciled

    def log_tail(self, job_id: str, *, max_bytes: int = 8192) -> str:
        job = self.status(job_id)
        if max_bytes <= 0 or not job.log_path.exists():
            return ""
        size = job.log_path.stat().st_size
        with job.log_path.open("rb") as fh:
            fh.seek(max(0, size - max_bytes))
            data = fh.read(max_bytes)
        return _redact(data.decode("utf-8", errors="replace"))

    def _init_db(self) -> None:
        self.studio_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.worker_dir.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    campaign_id TEXT,
                    draft_id TEXT,
                    request_key TEXT NOT NULL UNIQUE,
                    request_digest TEXT NOT NULL,
                    state TEXT NOT NULL,
                    pid INTEGER,
                    process_identity TEXT,
                    worker_nonce TEXT NOT NULL,
                    heartbeat_at TEXT,
                    command_version TEXT NOT NULL,
                    argv_json TEXT NOT NULL,
                    log_path TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    exit_code INTEGER,
                    error_code TEXT,
                    error_message TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_state ON jobs(state);
                CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at);
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _monitor(self, job_id: str) -> None:
        running = self._running[job_id]
        try:
            while True:
                try:
                    code = running.process.wait(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    self._heartbeat(job_id)
            self._finish_from_exit(job_id, code)
        finally:
            with self._running_lock:
                self._running.pop(job_id, None)

    def _monitor_detached_stop(self, job_id: str, pid: int | None, identity: str | None) -> None:
        while pid is not None and _is_same_process(pid, identity):
            if self.status(job_id).state != "stopping":
                return
            time.sleep(0.1)
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """UPDATE jobs SET state = 'interrupted', error_code = 'STOPPED',
                   updated_at = ?, finished_at = ?
                   WHERE job_id = ? AND state = 'stopping'""",
                (_ts(now), _ts(now), job_id),
            )

    def _heartbeat(self, job_id: str) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE jobs SET heartbeat_at = ?, updated_at = ? WHERE job_id = ?",
                (_ts(now), _ts(now), job_id),
            )

    def _finish_from_exit(self, job_id: str, exit_code: int) -> None:
        job = self.status(job_id)
        postcondition = self._postconditions.get(job.kind)
        if job.state == "stopping":
            next_state: JobState = "interrupted"
            error_code = "STOPPED"
        elif exit_code == 0 and (postcondition is None or postcondition(job)):
            next_state = "succeeded"
            error_code = None
        elif exit_code == 0:
            next_state = "failed"
            error_code = "POSTCONDITION_FAILED"
        else:
            next_state = "failed"
            error_code = "EXIT_NONZERO"
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                   SET state = ?, exit_code = ?, error_code = ?, updated_at = ?, finished_at = ?
                 WHERE job_id = ?
                """,
                (next_state, exit_code, error_code, _ts(now), _ts(now), job_id),
            )

    def _terminate(self, job_id: str, pid: int | None, identity: str | None) -> None:
        with self._running_lock:
            running = self._running.get(job_id)
        if running is not None:
            try:
                running.process.wait(timeout=self.stop_timeout + 2)
            except subprocess.TimeoutExpired:
                _terminate_pid(running.process.pid)
            return
        if pid is None or not _is_same_process(pid, identity):
            return
        _terminate_pid(pid)

    def _mark_failed_start(self, job_id: str, exc: BaseException) -> None:
        now = _utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                   SET state = 'failed', error_code = 'START_FAILED', error_message = ?,
                       updated_at = ?, finished_at = ?
                 WHERE job_id = ?
                """,
                (str(exc), _ts(now), _ts(now), job_id),
            )

    def _reconcile_state(self, job: JobRecord) -> JobState | None:
        if job.pid is None:
            return "unknown"
        alive = _is_same_process(job.pid, job.process_identity)
        if alive and self._writer_lock_is_held():
            return None
        if alive:
            return "unknown"
        if self._writer_lock_is_held():
            return "unknown"
        if job.state == "starting":
            return "failed"
        return "interrupted"

    def _writer_lock_is_held(self) -> bool:
        try:
            lock = self._try_writer_lock()
        except JobConflict:
            return True
        lock.close()
        return False

    def _wait_for_worker_lock(self, process: subprocess.Popen[bytes]) -> None:
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            if self._writer_lock_is_held() or process.poll() is not None:
                return
            time.sleep(0.02)

    def _try_writer_lock(self) -> _HeldWriterLock:
        self.writer_lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.writer_lock_path.open("a+b")
        try:
            _lock_file(fh)
        except OSError as exc:
            fh.close()
            raise JobConflict("another writer holds the Studio project lock") from exc
        fh.seek(1)
        fh.truncate()
        fh.write(f"pid {os.getpid()}".encode())
        fh.flush()
        return _HeldWriterLock(self.writer_lock_path, fh)

    def _job_row(self, conn: sqlite3.Connection, job_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if row is None:
            raise JobNotFound(job_id)
        return cast(sqlite3.Row, row)

    def _row_to_record(self, row: sqlite3.Row) -> JobRecord:
        kind = cast(JobKind, row["kind"])
        state = cast(JobState, row["state"])
        self._check_kind(kind)
        argv = tuple(json.loads(row["argv_json"]))
        return JobRecord(
            job_id=str(row["job_id"]),
            kind=kind,
            state=state,
            request_key=str(row["request_key"]),
            campaign_id=row["campaign_id"],
            draft_id=row["draft_id"],
            pid=None if row["pid"] is None else int(row["pid"]),
            process_identity=row["process_identity"],
            worker_nonce=str(row["worker_nonce"]),
            command_version=str(row["command_version"]),
            argv=argv,
            log_path=Path(row["log_path"]),
            created_at=_from_ts(str(row["created_at"])),
            updated_at=_from_ts(str(row["updated_at"])),
            started_at=_optional_ts(row["started_at"]),
            finished_at=_optional_ts(row["finished_at"]),
            heartbeat_at=_optional_ts(row["heartbeat_at"]),
            exit_code=None if row["exit_code"] is None else int(row["exit_code"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
        )

    @staticmethod
    def _check_kind(kind: str) -> None:
        if kind not in KINDS:
            raise ValueError(f"unknown job kind {kind!r}")

    @staticmethod
    def _check_argv(argv: Iterable[str]) -> tuple[str, ...]:
        checked = tuple(argv)
        if not checked:
            raise ValueError("argv must not be empty")
        if any(not isinstance(part, str) or not part for part in checked):
            raise ValueError("argv must contain only non-empty strings")
        return checked

    @staticmethod
    def _request_digest(
        *,
        kind: JobKind,
        argv: tuple[str, ...],
        campaign_id: str | None,
        draft_id: str | None,
        command_version: str,
    ) -> str:
        payload = {
            "argv": argv,
            "campaign_id": campaign_id,
            "command_version": command_version,
            "draft_id": draft_id,
            "kind": kind,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _add_src_to_env(env: dict[str, str]) -> None:
        src = str(Path(__file__).resolve().parents[2])
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = src if not existing else os.pathsep.join((src, existing))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _ts(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("Studio job timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat()


def _from_ts(value: str) -> datetime:
    return datetime.fromisoformat(value).astimezone(UTC)


def _optional_ts(value: str | None) -> datetime | None:
    return None if value is None else _from_ts(value)


def _to_db_path(path: Path) -> str:
    return str(path)


def _pid_to_db(pid: int) -> int:
    return int(pid)


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


def _process_identity(pid: int) -> str | None:
    if sys.platform == "win32":
        return _windows_creation_time(pid)
    stat = Path("/proc") / str(pid) / "stat"
    try:
        text = stat.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        after_comm = text.rsplit(") ", 1)[1]
        start_time = after_comm.split()[19]
    except IndexError:
        return None
    return f"proc-start:{start_time}"


def _windows_creation_time(pid: int) -> str | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    process_query_limited_information = 0x1000
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return None
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        if exit_code.value != 259:  # STILL_ACTIVE; a retained handle may outlive the process.
            return None
        creation = ctypes.c_ulonglong()
        exit_time = ctypes.c_ulonglong()
        kernel = ctypes.c_ulonglong()
        user = ctypes.c_ulonglong()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        return f"win-create:{creation.value}"
    finally:
        kernel32.CloseHandle(handle)


def _is_same_process(pid: int, identity: str | None) -> bool:
    current = _process_identity(pid)
    if current is None:
        return False
    return identity is not None and current == identity


def _terminate_pid(pid: int) -> None:
    try:
        if sys.platform == "win32":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True,
                check=False,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        else:
            os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except PermissionError:
        return


def _redact(text: str) -> str:
    redacted = text
    for name, value in os.environ.items():
        if len(value) >= 4 and any(
            part in name.upper() for part in ("API_KEY", "SECRET", "TOKEN", "PASSWORD")
        ):
            redacted = redacted.replace(value, "[REDACTED_VALUE]")
    for key in ("API_KEY", "SECRET", "TOKEN", "PASSWORD"):
        redacted = redacted.replace(key, "[REDACTED_KEY]")
    return redacted


def _worker_main(payload_path: Path) -> int:
    payload = json.loads(payload_path.read_text(encoding="utf-8"))
    lock_path = Path(payload["lock_path"])
    log_path = Path(payload["log_path"])
    argv = tuple(str(part) for part in payload["argv"])
    cwd = Path(payload["cwd"])
    stop_timeout = float(payload["stop_timeout"])
    stop_path = Path(payload["stop_path"])
    env = dict(os.environ)
    env.update({str(key): str(value) for key, value in payload["env"].items()})

    child: subprocess.Popen[bytes] | None = None
    stopping = False

    def request_stop(_signum: int, _frame: Any) -> None:
        nonlocal stopping
        stopping = True
        if child is not None and child.poll() is None:
            child.terminate()

    signal.signal(signal.SIGTERM, request_stop)
    if hasattr(signal, "SIGINT"):
        signal.signal(signal.SIGINT, request_stop)

    with _acquire_writer_lock(lock_path), log_path.open("ab") as log_file:
        child = subprocess.Popen(
            argv,
            cwd=cwd,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            shell=False,
            env=env,
        )
        while True:
            code = child.poll()
            if code is not None:
                return int(code)
            if stop_path.exists():
                stopping = True
                child.terminate()
            if stopping:
                try:
                    return int(child.wait(timeout=stop_timeout))
                except subprocess.TimeoutExpired:
                    child.kill()
                    return int(child.wait())
            time.sleep(0.1)


def _acquire_writer_lock(path: Path) -> _HeldWriterLock:
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 5.0
    while True:
        fh = path.open("a+b")
        try:
            _lock_file(fh)
            break
        except OSError:
            fh.close()
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.02)
    fh.seek(1)
    fh.truncate()
    fh.write(f"pid {os.getpid()} (studio worker)".encode())
    fh.flush()
    return _HeldWriterLock(path, fh)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--worker":
        try:
            return _worker_main(Path(args[1]))
        except OSError:
            return 73
    raise SystemExit("usage: python -m quantcrucible.studio.jobs --worker PAYLOAD")


if __name__ == "__main__":
    raise SystemExit(main())
