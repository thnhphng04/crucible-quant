from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from quantcrucible.studio.jobs import JobConflict, JobSupervisor, _process_identity


def test_stop_never_kills_a_reused_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    supervisor = JobSupervisor(tmp_path)
    job = supervisor.enqueue(
        kind="prepare_data",
        draft_id="d1",
        request_key="stale-pid",
        argv=(sys.executable, "-c", "print('unused')"),
    )
    with sqlite3.connect(supervisor.db_path) as conn:
        conn.execute(
            "UPDATE jobs SET state = 'unknown', pid = ?, process_identity = ? WHERE job_id = ?",
            (os.getpid(), "different-process", job.job_id),
        )
    monkeypatch.setattr(
        "quantcrucible.studio.jobs._terminate_pid",
        lambda _pid: pytest.fail("stale pid must not be killed"),
    )
    assert supervisor.stop(job.job_id).state == "interrupted"
    supervisor._finish_from_exit(job.job_id, 1)
    assert supervisor.status(job.job_id).state == "interrupted"


def test_log_redaction_hides_secret_values(monkeypatch: pytest.MonkeyPatch) -> None:
    from quantcrucible.studio.jobs import _redact

    monkeypatch.setenv("TRADING_API_KEY", "sensitive-value-123")
    assert "sensitive-value-123" not in _redact("failed: sensitive-value-123")


def _wait_for_state(
    supervisor: JobSupervisor, job_id: str, state: str, timeout: float = 5.0
) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if supervisor.status(job_id).state == state:
            return
        time.sleep(0.05)
    assert supervisor.status(job_id).state == state


def test_stop_after_restart_finalizes_without_the_original_monitor(tmp_path: Path) -> None:
    process = subprocess.Popen((sys.executable, "-c", "import time; time.sleep(30)"))
    try:
        supervisor = JobSupervisor(tmp_path, stop_timeout=0.2)
        job = supervisor.enqueue(
            kind="prepare_data",
            draft_id="d1",
            request_key="detached-stop",
            argv=(sys.executable, "-c", "print('unused')"),
        )
        with sqlite3.connect(supervisor.db_path) as conn:
            conn.execute(
                "UPDATE jobs SET state = 'running', pid = ?, process_identity = ? WHERE job_id = ?",
                (process.pid, _process_identity(process.pid), job.job_id),
            )
        supervisor.stop(job.job_id)
        _wait_for_state(supervisor, job.job_id, "interrupted")
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=5)


def test_enqueue_is_idempotent_for_the_same_request_key(tmp_path: Path) -> None:
    supervisor = JobSupervisor(tmp_path)
    argv = (sys.executable, "-c", "print('ok')")

    first = supervisor.enqueue(kind="prepare_data", draft_id="d1", request_key="same", argv=argv)
    again = supervisor.enqueue(kind="prepare_data", draft_id="d1", request_key="same", argv=argv)

    assert again.job_id == first.job_id
    assert again.state == "queued"

    with pytest.raises(JobConflict, match="different payload"):
        supervisor.enqueue(
            kind="prepare_data",
            draft_id="d1",
            request_key="same",
            argv=(sys.executable, "-c", "print('different')"),
        )


def test_start_runs_a_fixed_argv_subprocess_and_persists_success(tmp_path: Path) -> None:
    marker = tmp_path / "dataset.done"
    supervisor = JobSupervisor(
        tmp_path,
        postconditions={"prepare_data": lambda _job: marker.exists()},
    )
    job = supervisor.enqueue(
        kind="prepare_data",
        draft_id="d1",
        request_key="run",
        argv=(
            sys.executable,
            "-c",
            "from pathlib import Path; Path('dataset.done').write_text('ok'); print('done')",
        ),
    )

    running = supervisor.start(job.job_id)
    assert running.state == "running"
    assert running.pid is not None

    _wait_for_state(supervisor, job.job_id, "succeeded")
    finished = supervisor.status(job.job_id)
    assert finished.exit_code == 0
    assert finished.process_identity is not None
    assert "done" in supervisor.log_tail(job.job_id)


def test_nonzero_exit_is_failed(tmp_path: Path) -> None:
    supervisor = JobSupervisor(tmp_path)
    job = supervisor.enqueue(
        kind="evolve",
        campaign_id="c1",
        request_key="fail",
        argv=(sys.executable, "-c", "raise SystemExit(7)"),
    )

    supervisor.start(job.job_id)

    _wait_for_state(supervisor, job.job_id, "failed")
    failed = supervisor.status(job.job_id)
    assert failed.exit_code == 7
    assert failed.error_code == "EXIT_NONZERO"


def test_only_one_active_writer_can_start(tmp_path: Path) -> None:
    supervisor = JobSupervisor(tmp_path, stop_timeout=0.2)
    first = supervisor.enqueue(
        kind="evolve",
        campaign_id="c1",
        request_key="first",
        argv=(sys.executable, "-c", "import time; time.sleep(5)"),
    )
    second = supervisor.enqueue(
        kind="portfolio",
        campaign_id="c1",
        request_key="second",
        argv=(sys.executable, "-c", "print('second')"),
    )

    supervisor.start(first.job_id)

    with pytest.raises(JobConflict, match="only one writer"):
        supervisor.start(second.job_id)

    supervisor.stop(first.job_id)
    _wait_for_state(supervisor, first.job_id, "interrupted")


def test_stop_does_not_wait_for_the_writer_lock(tmp_path: Path) -> None:
    supervisor = JobSupervisor(tmp_path, stop_timeout=0.2)
    job = supervisor.enqueue(
        kind="evolve",
        campaign_id="c1",
        request_key="stop",
        argv=(sys.executable, "-c", "import time; time.sleep(5)"),
    )
    supervisor.start(job.job_id)

    started = time.monotonic()
    stopped = supervisor.stop(job.job_id)

    assert time.monotonic() - started < 1.0
    assert stopped.state in {"stopping", "interrupted"}
    _wait_for_state(supervisor, job.job_id, "interrupted")


def test_startup_reconcile_marks_dead_running_jobs_interrupted(tmp_path: Path) -> None:
    supervisor = JobSupervisor(tmp_path)
    job = supervisor.enqueue(
        kind="evolve",
        campaign_id="c1",
        request_key="dead",
        argv=(sys.executable, "-c", "print('never started here')"),
    )
    with sqlite3.connect(supervisor.db_path) as conn:
        conn.execute(
            """
            UPDATE jobs
               SET state = 'running', pid = ?, process_identity = ?, started_at = created_at
             WHERE job_id = ?
            """,
            (999999, "proc-start:missing", job.job_id),
        )

    reconciled = supervisor.reconcile_startup()

    assert [record.job_id for record in reconciled] == [job.job_id]
    assert supervisor.status(job.job_id).state == "interrupted"


def test_unknown_reconciled_job_blocks_new_starts(tmp_path: Path) -> None:
    supervisor = JobSupervisor(tmp_path)
    unknown = supervisor.enqueue(
        kind="evolve",
        campaign_id="c1",
        request_key="unknown",
        argv=(sys.executable, "-c", "print('unknown')"),
    )
    queued = supervisor.enqueue(
        kind="prepare_data",
        draft_id="d1",
        request_key="queued",
        argv=(sys.executable, "-c", "print('queued')"),
    )
    with sqlite3.connect(supervisor.db_path) as conn:
        conn.execute(
            "UPDATE jobs SET state = 'running', pid = NULL WHERE job_id = ?",
            (unknown.job_id,),
        )

    supervisor.reconcile_startup()

    assert supervisor.status(unknown.job_id).state == "unknown"
    with pytest.raises(JobConflict, match="only one writer"):
        supervisor.start(queued.job_id)


def test_restarted_supervisor_honors_child_owned_writer_lock(tmp_path: Path) -> None:
    supervisor = JobSupervisor(tmp_path, stop_timeout=0.2)
    running = supervisor.enqueue(
        kind="evolve",
        campaign_id="c1",
        request_key="running",
        argv=(sys.executable, "-c", "import time; time.sleep(5)"),
    )
    queued = supervisor.enqueue(
        kind="portfolio",
        campaign_id="c1",
        request_key="after-restart",
        argv=(sys.executable, "-c", "print('blocked')"),
    )

    supervisor.start(running.job_id)
    restarted = JobSupervisor(tmp_path, stop_timeout=0.2)

    reconciled = restarted.reconcile_startup()

    assert reconciled[0].job_id == running.job_id
    assert restarted.status(running.job_id).state == "running"
    with pytest.raises(JobConflict, match="only one writer"):
        restarted.start(queued.job_id)

    restarted.stop(running.job_id)
    _wait_for_state(restarted, running.job_id, "interrupted")
