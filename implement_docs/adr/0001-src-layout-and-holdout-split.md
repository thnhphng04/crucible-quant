# ADR-0001 — src layout, and holdout data separated from holdout code

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P0-01 · **Arch:** §5, §4.2, §3.3.1 · **Open item:** O7 (approach)

## Context

Arch §5 puts `core/`, `agent/`, `validation/`, `holdout/` … at the repository root, while the package is named `quantcrucible` and the template in §3.3.1 imports `from core.strategy.base`. Root-level top-level packages named `core`, `data`, `config` collide easily with other libraries and make an installable package awkward.

§5 also puts `evaluator_proc.py` (code, must be committed and tested) inside `holdout/` (data, `chmod 0400`, must never be committed or read by research processes). Mixing them means every tool that needs the code — editor, tests, the coding agent — has to open the folder that holds the data.

`chmod 0400` has no direct equivalent on Windows, where this project runs (A6).

## Decision

1. **src layout:** all code lives under `src/quantcrucible/` with the same subtree as §5 (`core/`, `agent/`, `validation/`, `ledger/`, `execution/`, `holdout/`, plus `config/` for the loader and `data/` for `DataSource` implementations). Imports are `quantcrucible.core.strategy.base`, etc. The strategy template's fixed region imports from `quantcrucible.core…`.
2. **Holdout split:**
   - root `holdout/` + `holdout.lock` = **data only**; gitignored; never visible to research processes, the sandbox, or the coding agent (`.claude/hooks/guard_paths.py`);
   - `src/quantcrucible/holdout/` = **code** (`evaluator_proc.py`, `campaign.py`); nothing else imports it (import-linter).
3. **Root data folders are anchored in `.gitignore`** (`/data/`, `/holdout/`, `/results/`) so the code packages `src/quantcrucible/data/` and `…/holdout/` are not ignored.
4. **Read-only holdout on Windows:** set the read-only attribute and an `icacls` deny-write ACE for the current user; on POSIX, `chmod 0400`. Protection is best-effort at the OS level — the **SHA256 in `holdout.lock`** checked by the evaluator before every use is the actual tamper detection (§4.2 layer 2).

## Consequences

- `pip install -e .` / `uv sync` gives a normal importable package; tests import the installed package, not the working tree by accident.
- Arch §5 now differs from the code tree: a one-line note under §5 (VI and EN) points here. [04-MODULE-MAP.md](../04-MODULE-MAP.md) holds the up-to-date tree.
- The §3.3.1 template example (`from core.strategy.base …`) becomes `from quantcrucible.core.strategy.base …` in code; the arch snippet is illustrative and stays as is.

## Alternatives considered

- **Flat layout as in §5** — rejected: generic top-level names (`core`, `data`, `config`), and no install boundary.
- **Keep `evaluator_proc.py` inside the data folder** — rejected: forces tooling and the agent into the holdout folder, which the guard hook must block outright.
