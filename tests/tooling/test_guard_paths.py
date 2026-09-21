"""The Claude Code guard hook (.claude/hooks/guard_paths.py) — P6 protection for the agent."""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
HOOK = ROOT / ".claude" / "hooks" / "guard_paths.py"


def run_hook(tool: str, tool_input: dict[str, Any], event: str = "PreToolUse") -> int:
    payload = {
        "hook_event_name": event,
        "tool_name": tool,
        "tool_input": tool_input,
        "cwd": str(ROOT),
    }
    proc = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
        env={**os.environ, "CLAUDE_PROJECT_DIR": str(ROOT)},
    )
    return proc.returncode


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Read", {"file_path": "holdout/btc_2025.parquet"}),
        ("Read", {"file_path": str(ROOT / "holdout" / "x.csv")}),
        ("Grep", {"pattern": "close", "path": "holdout"}),
        ("Write", {"file_path": "holdout.lock", "content": ""}),
        ("Write", {"file_path": "config/evaluation.lock.yaml", "content": ""}),
        ("Edit", {"file_path": "config/evaluation.lock.yaml"}),
        ("Bash", {"command": "cat holdout/btc_2025.parquet"}),
        ("Bash", {"command": "python -c 'open(\"./holdout/x.csv\")'"}),
        ("PowerShell", {"command": "Get-Content C:/Users/x/TradingProject/holdout/x.csv"}),
        ("Bash", {"command": "echo {} > config/evaluation.lock.yaml"}),
    ],
)
def test_blocks(tool: str, tool_input: dict[str, Any]) -> None:
    assert run_hook(tool, tool_input) == 2


@pytest.mark.parametrize(
    ("tool", "tool_input"),
    [
        ("Edit", {"file_path": "src/quantcrucible/holdout/evaluator_proc.py"}),
        ("Read", {"file_path": "config/evaluation.lock.yaml"}),
        ("Read", {"file_path": "config/user.yaml"}),
        ("Bash", {"command": "uv run pytest tests/holdout/"}),
        ("Bash", {"command": "cat config/evaluation.lock.yaml"}),
        ("Bash", {"command": "grep -rn evaluation.lock.yaml src/ | head"}),
        ("Bash", {"command": "uv run pytest -k lock > out.txt"}),
        ("Bash", {"command": "git commit -m 'holdout evaluator returns PASS/FAIL only'"}),
    ],
)
def test_allows(tool: str, tool_input: dict[str, Any]) -> None:
    assert run_hook(tool, tool_input) == 0


def test_reminds_after_architecture_edit() -> None:
    target = {"file_path": "research_docs_vi/Architecture_Design.md"}
    assert run_hook("Edit", target, event="PostToolUse") == 2
    assert run_hook("Edit", {"file_path": "README.md"}, event="PostToolUse") == 0


def test_reminds_after_implement_docs_edit() -> None:
    for path in ("implement_docs/01-ROADMAP-TASKS.md", "implement_docs_vi/adr/0000-mau.md"):
        assert run_hook("Write", {"file_path": path}, event="PostToolUse") == 2
    assert run_hook("Write", {"file_path": "src/quantcrucible/x.py"}, event="PostToolUse") == 0
