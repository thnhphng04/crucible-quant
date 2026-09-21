"""Boundaries that import-linter cannot express because they involve third-party packages.

Internal layer rules live in pyproject.toml ([tool.importlinter]);
see implement_docs/04-MODULE-MAP.md.
"""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "quantcrucible"

# A4: an LLM is called only from the agent layer — never at runtime.
LLM_SDKS = {"openai", "anthropic", "litellm", "langchain", "langgraph", "google.generativeai"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module)
    return found


def _is_llm_sdk(module: str) -> bool:
    return any(module == sdk or module.startswith(sdk + ".") for sdk in LLM_SDKS)


def test_purgedcv_only_in_wrapper_modules() -> None:
    """ADR-0007 / 02-CONVENTIONS: the rest of the code goes through the validation wrappers."""
    wrappers = {"validation/statistical.py", "validation/cpcv.py"}
    offenders = [
        f"{path.relative_to(SRC).as_posix()}: {module}"
        for path in SRC.rglob("*.py")
        if path.relative_to(SRC).as_posix() not in wrappers
        for module in _imported_modules(path)
        if module == "purgedcv" or module.startswith("purgedcv.")
    ]
    assert not offenders, f"purgedcv imported outside its wrappers (ADR-0007): {offenders}"


def test_llm_sdks_only_in_agent_layer() -> None:
    offenders = [
        f"{path.relative_to(SRC)}: {module}"
        for path in SRC.rglob("*.py")
        if path.relative_to(SRC).parts[0] != "agent"
        for module in _imported_modules(path)
        if _is_llm_sdk(module)
    ]
    assert not offenders, f"LLM SDK imported outside agent/ (violates A4): {offenders}"
