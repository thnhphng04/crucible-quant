# CLAUDE.md — Crucible Quant

An AI quantitative research lab: an LLM loop **generates deterministic rule-based trading strategies**, and a validation harness tries to **reject** them before any capital is at risk. Package: `quantcrucible` (src layout). Status: **phase 0 — harness first**; the tree under `src/` is still a skeleton.

**Talk to the user in Vietnamese.** Code, identifiers, commit messages, docstrings and `implement_docs/` are in English.

## Where the truth lives

1. `research_docs_vi/Architecture_Design.md` — **source of truth** for the design (Vietnamese).
2. `research_docs/Architecture_Design.md` — 1:1 English mirror. Read this one; cite sections as `§3.4`.
3. `implement_docs/` — how to build it: task list, conventions, invariant→test map, module map, ADRs.

If code and the architecture disagree, **stop and ask** — don't silently "fix" either side. An implementation-level deviation needs an ADR in `implement_docs/adr/`. An architecture-level change (principles, gates, thresholds, the decision register §10) is the user's call.

## Hard invariants — a change that breaks one is wrong, however good it looks

| Rule | Arch | Enforced by |
|---|---|---|
| **P1** Harness first: no agent code before the phase-0 gate passes (harness rejects all 4 leaky-oracle levels) | §1, §7 | roadmap order |
| **P2** Every backtest goes through the ledger. No bypass, no reset, no "quick test" path | §4.1 | ledger API is the only way to run a backtest |
| **P3** Strategies emit `Signal(direction, strength∈[0,1], stop_distance, take_profit)` — never lots/shares/contracts | §3.3 | types + import-linter |
| **P4** Vol targeting on every position; **volatility enters size exactly once**, the stop is a `min` cap | §3.4 | the ½-size test |
| **P5** Backtest ≡ live: same code path (NautilusTrader) | §3.5 | `execution` depends on `core` only |
| **P6** Holdout: written once, opened once **per campaign**, for one frozen `portfolio_hash`, returns PASS/FAIL only | §4.2 | DB PK + separate process + `.claude/hooks/guard_paths.py` |
| **A4** No LLM at runtime — LLM SDKs are imported only under `quantcrucible.agent` | §0 | `tests/test_architecture_boundaries.py` |
| **A7** Free data only | §6.1 | review |
| DSR runs on the **consolidated portfolio** with `N_eff` + `V[SR]` from `trials` (+ `portfolio_variants`) — never per cell | §3.1.6, §4.1 | gate ⑤ tests |
| Gate thresholds can only be **tightened**: `dsr_min ≥ 0.95`, `pbo_max ≤ 0.5` | §10.1 | config loader |
| `private` metrics never reach a prompt; prompts see `public` + `feedback` only | §3.3.2 | prompt-log test |
| Generated code runs only in the Docker sandbox (no network, empty env, no view of `holdout/`, `config/`, `ledger/`) | §3.3.3 | sandbox tests |
| No parameter optimizer inside the evolution loop; Optuna runs once, pre-freeze, and every evaluation is a `trials` row (`source='param_opt'`) | §3.3.1, §3.2.1 | ledger tests |

Full list with test names: [implement_docs/03-INVARIANT-TEST-MAP.md](implement_docs/03-INVARIANT-TEST-MAP.md).

## Never

- Read, list, grep or copy anything under the root `holdout/` or `holdout.lock` — seeing holdout data is itself leakage. The guard hook blocks it; don't work around it.
- Hand-edit `config/evaluation.lock.yaml` (generated per campaign from `config/user.yaml`).
- Loosen a gate threshold, reset the ledger, or delete `trials` rows.
- Add paid data sources, or AGPL dependencies (e.g. `pypbo`). `vectorbt` (Commons Clause) is for coarse screening only.
- Trust an external API from a doc snippet: snippets in the architecture that call libraries (`purgedcv`, NautilusTrader) are **unverified** (§10). Read the installed source first.

## Commands

```bash
uv sync                                   # install (Python 3.12, .venv)
uv run pytest                             # all tests (markers: slow, docker, network)
uv run pytest -m "not docker and not network and not slow"   # what CI runs
uv run ruff check . && uv run ruff format .
uv run mypy                               # strict
uv run lint-imports                       # layer contracts in pyproject.toml
uv run python scripts/check_doc_mirror.py # EN/VI mirror + broken links
```

Windows: set `PYTHONUTF8=1` if a tool crashes on Unicode output (cp1252 console).

## How to work

- Pick the next task from [implement_docs/01-ROADMAP-TASKS.md](implement_docs/01-ROADMAP-TASKS.md); each has an arch reference and an acceptance test. `/phase-check` reports progress.
- **Test first** for anything statistical (DSR, PBO, CPCV, N_eff), sizing, or safety-related (gates, holdout, sandbox). Numerical tests use published reference values, not values the code just produced.
- Keep changes small, one task per branch. Commit or push only when the user asks.
- Heavy dependencies are added by the task that first needs them, with a pinned version and a license check ([02-CONVENTIONS.md](implement_docs/02-CONVENTIONS.md)).
- Create files with the Write/Edit tools, not shell heredocs — the guard hook scans shell commands for holdout paths.
- Implementation decisions → `/new-adr`. Unresolved questions → [05-OPEN-ITEMS.md](implement_docs/05-OPEN-ITEMS.md).

## Editing the design docs

Edit `research_docs_vi/` first, mirror the change into `research_docs/` line-for-line (same line count, same headings), then run `scripts/check_doc_mirror.py` (or `/sync-docs`). Source reports `90`–`93` are Vietnamese only and are never translated.
