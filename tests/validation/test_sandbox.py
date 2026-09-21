"""Sandbox for generated code (Architecture §3.3.3, ADR-0004) — INV-36.

Unit tests run everywhere. Tests marked ``docker`` start real containers running probe code at
module level (the first thing the runner executes) and read what it saw from the error report.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from quantcrucible.core.strategy.base import generate_signals
from quantcrucible.core.strategy.template import load_strategy_class
from quantcrucible.execution.engine import run_backtest
from quantcrucible.execution.risk import RiskSettings
from quantcrucible.ledger.db import Ledger
from quantcrucible.ledger.records import Event
from quantcrucible.validation import sandbox_runner
from quantcrucible.validation.gates import G1B_DYNAMIC, GateContext, GatePipeline
from quantcrucible.validation.sandbox import (
    NO_REPORT,
    OS_ACCESS,
    RESOURCE_LIMIT,
    TIMEOUT,
    SandboxJob,
    SandboxLimits,
    SandboxResult,
    SandboxRunner,
    sandbox_failure,
    source_hash,
    truncate,
)
from tests.factories import make_bars
from tests.validation.test_gates import FakeGate, candidate

ROOT = Path(__file__).resolve().parents[2]
ZOO = (ROOT / "src/quantcrucible/core/zoo/ema_crossover.py").read_text(encoding="utf-8")
PARAMS = {"fast": 10, "slow": 50, "k_atr": 2.0}


def job(source: str = ZOO, kind: str = "signals", n: int = 120) -> SandboxJob:
    options = {"lookback": 400, "risk": asdict(RiskSettings())}
    return SandboxJob(kind, source, {"BTC/USDT": make_bars(n)}, PARAMS, options)


# ── unit: no Docker needed ────────────────────────────────────────────────


def test_command_isolation_flags(tmp_path: Path) -> None:
    cmd = SandboxRunner("img:tag").command("qc-x", tmp_path)
    joined = " ".join(cmd)
    for flag in (
        "--rm", "--network none", "--read-only", "--cap-drop ALL",
        "--security-opt no-new-privileges", "--pids-limit 256", "--memory 2g",
        "--memory-swap 2g", "--cpus 1.0", "--ipc none",
    ):  # fmt: skip
        assert flag in joined, flag
    mounts = [cmd[i + 1] for i, c in enumerate(cmd) if c == "-v"]
    assert [m.split(":")[-2:] for m in mounts] == [["/job/in", "ro"], ["/job/out", "rw"]]
    assert "-e" not in cmd and "--env" not in cmd and "--env-file" not in cmd
    assert cmd[-2:] == ["img:tag", "/job"]


def test_prepare_stages_only_the_job(tmp_path: Path) -> None:
    SandboxRunner("img").prepare(job(), tmp_path)
    files = sorted(p.relative_to(tmp_path).as_posix() for p in tmp_path.rglob("*") if p.is_file())
    assert files == ["in/data/BTC-USDT_1d.parquet", "in/job.json", "in/strategy.py"]
    spec = json.loads((tmp_path / "in/job.json").read_text(encoding="utf-8"))
    assert spec["symbols"] == {"BTC/USDT": "BTC-USDT_1d.parquet"}
    assert spec["kind"] == "signals" and spec["params"] == PARAMS


def test_runner_on_host_with_trusted_source(tmp_path: Path) -> None:
    SandboxRunner("img").prepare(job(kind="signals"), tmp_path)
    assert sandbox_runner.main([str(tmp_path)]) == 0
    report = json.loads((tmp_path / "out/report.json").read_text(encoding="utf-8"))
    expected = generate_signals(load_strategy_class(ZOO)(PARAMS), make_bars(120), 400)
    assert [s[0] for s in report["result"]["signals"]["BTC/USDT"]] == [
        s.direction for s in expected
    ]


@pytest.mark.parametrize(
    ("source", "error_type", "os_error"),
    [
        ("raise SystemExit(3)\n", "SystemExit", False),
        ("open('/definitely/not/here')\n", "FileNotFoundError", True),
        ("x = 1\n", "TemplateError", False),
    ],
)
def test_runner_reports_every_failure(
    tmp_path: Path, source: str, error_type: str, os_error: bool
) -> None:
    SandboxRunner("img").prepare(job(source), tmp_path)
    assert sandbox_runner.main([str(tmp_path)]) == 1
    report = json.loads((tmp_path / "out/report.json").read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["error_type"] == error_type
    assert report["os_error"] is os_error


def test_truncate() -> None:
    assert truncate(b"abc", 10) == "abc"
    out = truncate(b"x" * 100, 10)
    assert out.startswith("x" * 10) and "truncated 90 bytes" in out


def test_source_hash_ignores_line_endings(tmp_path: Path) -> None:
    for crlf in (False, True):
        root = tmp_path / str(crlf)
        (root / "docker").mkdir(parents=True)
        (root / "src/pkg").mkdir(parents=True)
        nl = "\r\n" if crlf else "\n"
        for rel in ("docker/sandbox.Dockerfile", "pyproject.toml", "uv.lock", "README.md"):
            (root / rel).write_bytes(f"a{nl}b{nl}".encode())
        (root / "src/pkg/m.py").write_bytes(f"x = 1{nl}".encode())
    assert source_hash(tmp_path / "False") == source_hash(tmp_path / "True")
    (tmp_path / "True/src/pkg/m.py").write_text("x = 2\n")
    assert source_hash(tmp_path / "False") != source_hash(tmp_path / "True")


def _result(violation: str | None, report: dict[str, object] | None = None) -> SandboxResult:
    return SandboxResult(False, report, "", "err", 137, violation == TIMEOUT, violation, 1.0)


def test_violations_are_logged_as_sandbox_violation(tmp_path: Path) -> None:
    ledger = Ledger.open(tmp_path / "ledger.db")
    ledger.open_campaign("c1", "2025/2026", lock_hash="h")
    gate = FakeGate(G1B_DYNAMIC, lambda: sandbox_failure(G1B_DYNAMIC, _result(TIMEOUT)))
    GatePipeline([gate]).run(candidate(), GateContext(ledger=ledger, lock={}))
    assert [e for e, _ in ledger.events("c1")][-1] == Event.SANDBOX_VIOLATION


def test_strategy_errors_keep_the_gate_event() -> None:
    report = {"ok": False, "error": "ValueError: boom", "os_error": False}
    res = sandbox_failure(G1B_DYNAMIC, _result(None, report))
    assert res.event is None and res.reason == "sandbox: ValueError: boom"


# ── docker: real containers ───────────────────────────────────────────────


@pytest.fixture
def image(sandbox_image: str) -> str:
    return sandbox_image


def run(image: str, source: str, limits: SandboxLimits | None = None) -> SandboxResult:
    return SandboxRunner(image, limits).run(job(source))


def probe_error(image: str, source: str) -> str:
    res = run(image, source)
    assert res.report is not None and res.report["ok"] is False, res
    return str(res.report["error"])


NETWORK_PROBE = """
import socket
try:
    socket.create_connection(("1.1.1.1", 53), timeout=3)
    raise RuntimeError("NETWORK_OPEN")
except OSError as e:
    try:
        socket.getaddrinfo("example.com", 443)
        raise RuntimeError("DNS_OPEN")
    except OSError:
        raise RuntimeError("NETWORK_BLOCKED " + type(e).__name__) from None
"""

ENV_PROBE = """
import os
raise RuntimeError("ENV=" + repr(sorted(os.environ)))
"""

FS_PROBE = """
import os
found = []
for d, dirs, files in os.walk("/"):
    dirs[:] = [x for x in dirs if os.path.join(d, x) not in ("/proc", "/sys", "/dev")]
    for f in files:
        if f.endswith((".parquet", ".db", ".sqlite", ".lock", ".yaml")) and not d.startswith(
            ("/opt/venv", "/usr", "/etc")
        ):
            found.append(os.path.join(d, f))
raise RuntimeError("SEEN=" + repr({"job": sorted(os.listdir("/job")),
    "in": sorted(os.listdir("/job/in")), "files": sorted(found)}))
"""

WRITE_PROBE = """
out = {}
for p in ("/x", "/etc/x", "/opt/venv/x", "/job/in/x", "/tmp/x"):
    try:
        with open(p, "w") as fh:
            fh.write("1")
        out[p] = "WROTE"
    except OSError:
        out[p] = "DENIED"
raise RuntimeError("WRITES=" + repr(out))
"""


@pytest.mark.docker
def test_docker_runs_signals_and_backtest_like_the_host(image: str) -> None:
    res = SandboxRunner(image).run(job(kind="backtest", n=300))
    assert res.ok, res
    host = run_backtest(load_strategy_class(ZOO)(PARAMS), {"BTC/USDT": make_bars(300)})
    assert res.report is not None
    np.testing.assert_allclose(res.report["result"]["equity"], host.equity, rtol=1e-12)


@pytest.mark.docker
def test_docker_grid_backtest_matches_host_runs(image: str) -> None:
    """Gate ④'s job: one backtest per configuration, identical to running each on the host."""
    grid = [PARAMS, {**PARAMS, "fast": 14}, {**PARAMS, "slow": 130}]
    base = job(kind="grid_backtest", n=300)
    res = SandboxRunner(image).run(
        SandboxJob(base.kind, base.source, base.bars, PARAMS, {**base.options, "grid": grid})
    )
    assert res.ok, res
    assert res.report is not None
    out = res.report["result"]
    assert len(out["returns"]) == 3 and len(out["ts"]) == 299
    for params, row in zip(grid, out["returns"], strict=True):
        host = run_backtest(load_strategy_class(ZOO)(params), {"BTC/USDT": make_bars(300)})
        np.testing.assert_allclose(row, host.returns, rtol=1e-12)


@pytest.mark.docker
def test_docker_no_network(image: str) -> None:
    assert probe_error(image, NETWORK_PROBE).startswith("RuntimeError: NETWORK_BLOCKED")


@pytest.mark.docker
def test_docker_empty_env(image: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QC_FAKE_API_KEY", "sk-should-never-leak")
    # LC_CTYPE is set by Python itself at startup (PEP 538 C-locale coercion), not inherited
    assert probe_error(image, ENV_PROBE) in (
        "RuntimeError: ENV=[]",
        "RuntimeError: ENV=['LC_CTYPE']",
    )


@pytest.mark.docker
def test_docker_sees_only_the_job(image: str) -> None:
    seen = eval(probe_error(image, FS_PROBE).removeprefix("RuntimeError: SEEN="))
    assert seen == {
        "job": ["in", "out"],
        "in": ["data", "job.json", "strategy.py"],
        "files": ["/job/in/data/BTC-USDT_1d.parquet"],
    }


@pytest.mark.docker
def test_docker_writes_only_to_tmp(image: str) -> None:
    writes = eval(probe_error(image, WRITE_PROBE).removeprefix("RuntimeError: WRITES="))
    assert writes.pop("/tmp/x") == "WROTE"
    assert set(writes.values()) == {"DENIED"}


@pytest.mark.docker
def test_docker_timeout_kills(image: str) -> None:
    res = run(image, "while True:\n    pass\n", SandboxLimits(timeout_s=5))
    assert res.timed_out and res.violation == TIMEOUT and not res.ok
    assert res.elapsed_s < 40
    assert res.container.startswith("qc-sandbox-")
    left = subprocess.run(  # only this job's container: other runs may share the image
        ["docker", "ps", "-aq", "--filter", f"name=^/{res.container}$"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    assert left == []


@pytest.mark.docker
def test_docker_output_truncated(image: str) -> None:
    source = (
        "import sys\nprint('x' * 200_000)\nsys.stderr.write('y' * 200_000)\nraise SystemExit(1)\n"
    )
    res = run(image, source, SandboxLimits(output_bytes=1_000))
    assert res.stdout.startswith("x" * 1_000) and "truncated" in res.stdout
    assert len(res.stdout) < 1_100 and len(res.stderr) < 1_100


@pytest.mark.docker
def test_docker_memory_limit(image: str) -> None:
    res = run(image, "b = bytearray(2_000_000_000)\n", SandboxLimits(memory="256m"))
    assert not res.ok
    assert res.violation in (RESOURCE_LIMIT, OS_ACCESS, NO_REPORT) or (
        res.report is not None and res.report.get("error_type") == "MemoryError"
    )


@pytest.mark.docker
def test_docker_os_error_is_a_violation(image: str) -> None:
    res = run(image, "open('/job/in/strategy.py', 'w')\n")
    assert res.violation == OS_ACCESS and not res.ok
