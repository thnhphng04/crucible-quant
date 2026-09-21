"""Host side of the sandbox for generated code (Architecture §3.3.3, ADR-0004).

Every job runs in a fresh Docker container: no network, an empty environment, a read-only root
file system, a size-capped ``/tmp``, RAM/CPU/PID limits and a wall-clock timeout. The container
sees exactly two host folders: ``/job/in`` (read-only — the strategy source, ``job.json`` and the
job's IS slice, written here) and ``/job/out`` (the report). The data folder, ``holdout/``,
``config/`` and ``ledger/`` are never mounted.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any

from quantcrucible.core.strategy.base import Bars
from quantcrucible.data.store import file_name, write_bars
from quantcrucible.ledger.records import Event
from quantcrucible.validation.gates import GateResult

IMAGE_REPO = "quantcrucible-sandbox"
DOCKERFILE = Path("docker") / "sandbox.Dockerfile"
MAX_REPORT_BYTES = 64 * 2**20
TIMEOUT = "timeout"
RESOURCE_LIMIT = "resource_limit"
NO_REPORT = "no_report"
OS_ACCESS = "os_access"


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    timeout_s: float = 300.0  # arch §3.3.3 default
    memory: str = "2g"
    cpus: float = 1.0
    pids: int = 256
    tmp_size: str = "256m"
    output_bytes: int = 4_096  # stdout / stderr kept for feedback, each


@dataclass(frozen=True, slots=True)
class SandboxJob:
    kind: str  # signals | backtest | grid_backtest | leak_check
    source: str
    bars: Mapping[str, Bars]  # the job's IS slice — the only data the container will see
    params: Mapping[str, float | int] = field(default_factory=dict)
    options: Mapping[str, Any] = field(default_factory=dict)  # lookback, costs, seed, …
    timeout_s: float | None = None  # overrides SandboxLimits.timeout_s (e.g. gate ④'s grid)


@dataclass(frozen=True, slots=True)
class SandboxResult:
    ok: bool
    report: dict[str, Any] | None
    stdout: str
    stderr: str
    exit_code: int | None
    timed_out: bool
    violation: str | None  # timeout | resource_limit | no_report | os_access
    elapsed_s: float
    container: str = ""  # docker name, unique per job

    @property
    def error(self) -> str:
        if self.report and not self.ok:
            return str(self.report.get("error", "error"))
        return self.violation or ""


def source_hash(root: Path) -> str:
    """Hash of everything baked into the image: Dockerfile, project metadata, lockfile, src."""
    h = hashlib.sha256()
    files = [root / DOCKERFILE, root / "pyproject.toml", root / "uv.lock", root / "README.md"]
    files += sorted(
        p
        for p in (root / "src").rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    )
    for p in files:
        h.update(p.relative_to(root).as_posix().encode())
        h.update(b"\0")
        h.update(p.read_bytes().replace(b"\r\n", b"\n"))  # same tag on Windows and Linux
        h.update(b"\0")
    return h.hexdigest()[:16]


def image_tag(root: Path) -> str:
    return f"{IMAGE_REPO}:{source_hash(root)}"


def ensure_image(root: Path, docker: str = "docker") -> str:
    """Build the sandbox image for the current sources unless it already exists."""
    tag = image_tag(root)
    probe = subprocess.run([docker, "image", "inspect", tag], capture_output=True, check=False)
    if probe.returncode != 0:
        subprocess.run(
            [docker, "build", "-f", str(DOCKERFILE), "-t", tag, "."],
            cwd=root,
            check=True,
            capture_output=True,
        )
    return tag


def truncate(data: bytes, limit: int, total: int | None = None) -> str:
    total = len(data) if total is None else total
    text = data[:limit].decode("utf-8", errors="replace")
    return text + (f"\n…[truncated {total - limit} bytes]" if total > limit else "")


class _CappedReader(threading.Thread):
    """Drain a pipe, keeping only the first ``limit`` bytes (output can be endless)."""

    def __init__(self, pipe: IO[bytes], limit: int) -> None:
        super().__init__(daemon=True)
        self.pipe, self.limit = pipe, limit
        self.kept = bytearray()
        self.total = 0

    def run(self) -> None:
        while chunk := self.pipe.read(65_536):
            self.total += len(chunk)
            room = self.limit - len(self.kept)
            if room > 0:
                self.kept += chunk[:room]

    def text(self) -> str:
        return truncate(bytes(self.kept), self.limit, self.total)


class SandboxRunner:
    def __init__(
        self,
        image: str,
        limits: SandboxLimits | None = None,
        docker: str = "docker",
        work_root: Path | None = None,
    ) -> None:
        self.image = image
        self.limits = limits or SandboxLimits()
        self.docker = docker
        self.work_root = work_root or Path(tempfile.gettempdir()) / "quantcrucible-sandbox"

    # ── host-side staging ────────────────────────────────────────────────
    def prepare(self, job: SandboxJob, job_dir: Path) -> None:
        inp, out = job_dir / "in", job_dir / "out"
        (inp / "data").mkdir(parents=True)
        out.mkdir()
        timeframes = {b.timeframe for b in job.bars.values()}
        if len(timeframes) != 1:
            raise ValueError(f"a job needs exactly one timeframe, got {timeframes}")
        symbols: dict[str, str] = {}
        for symbol, bars in job.bars.items():
            name = file_name(symbol, bars.timeframe)
            write_bars(inp / "data" / name, bars)
            symbols[symbol] = name
        spec = {
            **dict(job.options),
            "kind": job.kind,
            "params": dict(job.params),
            "symbols": symbols,
            "timeframe": timeframes.pop(),
        }
        spec.setdefault("lookback", 400)
        (inp / "job.json").write_text(json.dumps(spec, sort_keys=True), encoding="utf-8")
        (inp / "strategy.py").write_text(job.source, encoding="utf-8")

    def command(self, name: str, job_dir: Path) -> list[str]:
        lim = self.limits
        cmd = [
            self.docker, "run", "--rm", "--name", name,
            "--network", "none",
            "--read-only", "--tmpfs", f"/tmp:rw,nosuid,nodev,size={lim.tmp_size}",
            "--memory", lim.memory, "--memory-swap", lim.memory,
            "--cpus", str(lim.cpus), "--pids-limit", str(lim.pids),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
            "--ipc", "none",
            "-v", f"{(job_dir / 'in').resolve()}:/job/in:ro",
            "-v", f"{(job_dir / 'out').resolve()}:/job/out:rw",
        ]  # fmt: skip
        if sys.platform != "win32":  # Linux host: run as the owner of the job folders
            cmd += ["--user", f"{os.getuid()}:{os.getgid()}"]
        return [*cmd, self.image, "/job"]

    # ── run ──────────────────────────────────────────────────────────────
    def run(self, job: SandboxJob) -> SandboxResult:
        self.work_root.mkdir(parents=True, exist_ok=True)
        job_dir = Path(tempfile.mkdtemp(prefix="job-", dir=self.work_root))
        name = f"qc-sandbox-{uuid.uuid4().hex[:12]}"
        try:
            self.prepare(job, job_dir)
            return self._run_container(name, job_dir, job.timeout_s or self.limits.timeout_s)
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)

    def _run_container(self, name: str, job_dir: Path, timeout_s: float) -> SandboxResult:
        lim = self.limits
        start = time.monotonic()
        proc = subprocess.Popen(
            self.command(name, job_dir),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert proc.stdout is not None and proc.stderr is not None
        readers = [_CappedReader(p, lim.output_bytes) for p in (proc.stdout, proc.stderr)]
        for r in readers:
            r.start()
        timed_out = False
        try:
            proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            subprocess.run([self.docker, "kill", name], capture_output=True, check=False)
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        for r in readers:
            r.join(timeout=5)
        elapsed = time.monotonic() - start
        report = self._read_report(job_dir / "out" / "report.json")
        code = proc.returncode
        violation: str | None = None
        if timed_out:
            violation = TIMEOUT
        elif code == 137:  # SIGKILL from the memory / pids limits
            violation = RESOURCE_LIMIT
        elif report is None:
            violation = NO_REPORT
        elif report.get("os_error"):
            violation = OS_ACCESS
        ok = bool(report and report.get("ok")) and violation is None
        return SandboxResult(
            ok=ok,
            report=report,
            stdout=readers[0].text(),
            stderr=readers[1].text(),
            exit_code=code,
            timed_out=timed_out,
            violation=violation,
            elapsed_s=elapsed,
            container=name,
        )

    @staticmethod
    def _read_report(path: Path) -> dict[str, Any] | None:
        try:
            if not path.is_file() or path.stat().st_size > MAX_REPORT_BYTES:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None


def sandbox_failure(gate: str, result: SandboxResult) -> GateResult:
    """The rejection a gate returns when its sandbox job failed. OS-level trouble (timeout,
    resource limits, missing report, touching the network or files) is logged as
    ``SANDBOX_VIOLATION``; an ordinary exception in the strategy keeps the gate's own event."""
    detail: dict[str, Any] = {
        "violation": result.violation,
        "exit_code": result.exit_code,
        "stderr": result.stderr,
    }
    if result.report and not result.report.get("ok"):
        detail["traceback"] = result.report.get("traceback")
    return GateResult(
        passed=False,
        gate=gate,
        value=None,
        reason=f"sandbox: {result.error}",
        detail=detail,
        event=Event.SANDBOX_VIOLATION if result.violation else None,
    )
