"""Claude Code hook: keep the coding agent away from the holdout and the evaluation lock.

P6 (Architecture §1, §4.2): the holdout is read exactly once per campaign, by the evaluator
process. A coding agent that reads holdout data — even "just to check the format" — leaks it
into every later design decision. So:

  PreToolUse  — BLOCK (exit 2) any tool call that touches root holdout/ or holdout.lock, and any
                write to config/evaluation.lock.yaml (it is generated, §10.1).
  PostToolUse — REMIND (exit 2 feeds stderr back to the agent; the edit already happened) after an
                Architecture_Design.md edit: mirror VI → EN and run scripts/check_doc_mirror.py.

Reads the hook JSON from stdin. Exit 0 = allow.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Any

WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SHELL_TOOLS = {"Bash", "PowerShell"}

# Root-level holdout/ only — src/quantcrucible/holdout/ and tests/holdout/ are ordinary code.
SHELL_HOLDOUT = re.compile(
    r"(?:^|[\s'\"=(;|&])(?:\./|\.\\)?holdout(?:[/\\]|\.lock\b)"
    r"|TradingProject[/\\]holdout(?:[/\\]|\.lock\b)",
    re.IGNORECASE,
)
# A redirect or write verb whose target is the lock file — not a mere mention of its name
# (code and tests legitimately refer to it).
SHELL_LOCK_WRITE = re.compile(
    r"(?:>>?|\b(?:rm|mv|cp|del|tee|touch|truncate|chmod|attrib|icacls|sed\s+-i|Set-Content|"
    r"Add-Content|Out-File|Remove-Item|Move-Item|Copy-Item|New-Item|Clear-Content)\b)"
    r"[^|;&\n<>]*?evaluation\.lock\.yaml",
    re.IGNORECASE,
)
# ASCII only: hook stderr is decoded with the console code page on Windows.
LOCK_REASON = (
    "config/evaluation.lock.yaml is generated from config/user.yaml per campaign - "
    "edit user.yaml instead, never the lock (Architecture 10.1)"
)


def _project_root(payload: dict[str, Any]) -> Path:
    root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or "."
    return Path(root).resolve()


def _rel(path_str: str, root: Path) -> str | None:
    """Project-relative POSIX path, lower-cased (Windows paths are case-insensitive)."""
    p = Path(path_str)
    if not p.is_absolute():
        p = root / p
    try:
        return p.resolve().relative_to(root).as_posix().lower()
    except ValueError:
        return None  # outside the project


def _is_holdout(rel: str) -> bool:
    return rel == "holdout" or rel.startswith("holdout/") or rel == "holdout.lock"


def _target_path(tool_input: dict[str, Any]) -> str | None:
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def pre_tool_use(tool: str, tool_input: dict[str, Any], root: Path) -> str | None:
    """Return a block reason, or None to allow."""
    if tool in SHELL_TOOLS:
        cmd = str(tool_input.get("command", ""))
        if SHELL_HOLDOUT.search(cmd):
            return "the command touches the root holdout/ (P6: only the evaluator process reads it)"
        if SHELL_LOCK_WRITE.search(cmd):
            return LOCK_REASON
        return None

    target = _target_path(tool_input)
    if target is None:
        return None
    rel = _rel(target, root)
    if rel is None:
        return None
    if _is_holdout(rel):
        return f"{rel} is holdout data (P6: only the evaluator process reads it, once per campaign)"
    if tool in WRITE_TOOLS and rel == "config/evaluation.lock.yaml":
        return LOCK_REASON
    return None


def post_tool_use(tool: str, tool_input: dict[str, Any], root: Path) -> str | None:
    """Return a reminder for the agent, or None."""
    if tool not in WRITE_TOOLS:
        return None
    target = _target_path(tool_input)
    rel = _rel(target, root) if target else None
    if rel in {"research_docs_vi/architecture_design.md", "research_docs/architecture_design.md"}:
        return (
            "Architecture_Design.md changed. research_docs_vi/ is the source of truth and "
            "research_docs/ must stay a 1:1 mirror (same line count, same headings). "
            "Apply the same change to the other language, then run "
            "`uv run python scripts/check_doc_mirror.py`."
        )
    return None


def main() -> int:
    try:
        payload: dict[str, Any] = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0  # never break the session on malformed input
    tool = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    root = _project_root(payload)
    event = payload.get("hook_event_name", "PreToolUse")

    if event == "PostToolUse":
        message = post_tool_use(tool, tool_input, root)
        if message:
            print(message, file=sys.stderr)
            return 2
        return 0

    reason = pre_tool_use(tool, tool_input, root)
    if reason:
        print(f"Blocked by .claude/hooks/guard_paths.py: {reason}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
