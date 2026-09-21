# 02 — Conventions

Rules for all code in `src/`, `tests/` and `scripts/`. Tooling enforces what it can (`pyproject.toml`); the rest is review.

## Layout & tooling

- Python **3.12**, managed by **uv** (`uv sync`, `uv add <pkg>`, `uv.lock` committed).
- src layout: `src/quantcrucible/…`. Module placement and allowed imports: [04-MODULE-MAP.md](04-MODULE-MAP.md) (enforced by import-linter).
- `ruff` (lint + format, line length 100), `mypy --strict` over `src`, `tests`, `scripts`. No `# type: ignore` without a reason comment.
- CI runs exactly the commands in [CLAUDE.md](../CLAUDE.md). A task is not done until they pass.

## Types & contracts

- Contracts are `Protocol`s; value objects are `@dataclass(frozen=True, slots=True)`. Validate invariants in `__post_init__` (e.g. `Signal.strength ∈ [0,1]`).
- Names match the architecture: `Signal`, `EvaluationReport`, `GateResult`, `portfolio_hash`, `campaign_id`, table/column names from §4. Gate ids: `g0_drift_static`, `g1a_static`, `g0_drift_trace`, `g1b_dynamic`, `g2_minbtl`, `g3_is`, `g4_pbo`, `g5_dsr`, `g6p_robustness`, `g6_holdout`, `g7_dryrun`.
- No `dict[str, Any]` crossing module boundaries except where the arch stores JSON (`params`, `detail`).

## Time, data, numbers

- **Timestamps are timezone-aware UTC** (`datetime.now(UTC)`); ruff `DTZ` bans naive datetimes. Bars are indexed by bar **close** time.
- **Causality:** a value at time *t* may use only data with timestamp ≤ *t*. Every indicator ships with the generic causality test (P0-04).
- Signals from bar *t* execute at bar *t+1* (P0-10). Never trade on the bar that produced the signal.
- Floats for statistics and returns. `Decimal` only at the venue boundary (`round_to_lot`, order quantities, prices sent to Nautilus).
- Returns are simple returns unless a function's name says `log_`. Annualization factor is explicit (`periods_per_year`), never inferred silently.

## Determinism & reproducibility

- No global RNG. Anything random takes a `seed: int` (or a `numpy.random.Generator`) and the seed is recorded in the ledger (`seed` column).
- `strategy_hash` = SHA256 of the **normalized** code (AST dump without comments/whitespace), so reformatting is not a new trial.
- A backtest is reproducible from its ledger row: code hash, params, universe, timeframe, timerange, seed, lock hash.

## Errors & logging

- Gates **fail closed**: any exception ⇒ `REJECT` + ledger row with the traceback in `detail`. Never swallow an exception in validation code.
- Raise specific exceptions (`HoldoutAccessError`, `LockMismatchError`, `LedgerAppendOnlyError`…) defined next to the code that raises them.
- stdlib `logging` only (`logger = logging.getLogger(__name__)`); `print` is banned in `src/` (ruff `T20`). Logs are not a data channel — anything the system decides on goes through the ledger.
- Never log holdout values, `private` metrics into anything a prompt builder can read, or secrets.

## Secrets & config

- API keys only via environment / `.env` (gitignored). Never in `config/user.yaml`, never in the ledger, never passed into the sandbox.
- Research parameters come **only** from the campaign's `evaluation.lock.yaml` at runtime — not from `user.yaml` directly, not from constants in code.

## Tests

- pytest; `tests/` mirrors `src/quantcrucible/` (`tests/ledger/test_db.py` ↔ `ledger/db.py`). End-to-end tests in `tests/e2e/`.
- Markers: `slow`, `docker` (needs Docker daemon), `network` (hits the internet — never in CI). CI runs `-m "not docker and not network and not slow"`; run the docker tests locally before merging sandbox changes.
- **Statistical code is tested against published reference values** (papers' numerical examples) and simulation properties (e.g. PBO ≈ 0.5 on noise) — never against numbers the implementation itself produced.
- Safety invariants get a test that tries to **break** them (raw SQL delete, byte-flip in the fixed region, second holdout opening).
- Tests never touch the real `holdout/`, `data/` or ledger: use `tmp_path` fixtures and synthetic data.

## Dependencies

Add a dependency only in the task that needs it, with:
1. a pinned version range in `pyproject.toml` (`uv add 'pkg>=x.y,<x.(y+1)'` for 0.x libraries);
2. a license check — **no AGPL/GPL** in `src/` (e.g. `pypbo` is out); flag Commons Clause (`vectorbt`: coarse screening only, never in the validation path);
3. an API check against the installed source before wrapping it (the arch snippets are unverified, §10);
4. a thin wrapper module so the rest of the code doesn't import it directly (e.g. `validation/statistical.py` wraps `purgedcv`).

LLM SDKs may be imported only under `quantcrucible.agent` (A4; `tests/test_architecture_boundaries.py`).

## Git

- One task per branch: `p0-02-ledger`, `p1-06-sizing`, `chore/…`, `docs/…`.
- Commit messages: imperative subject ≤ 72 chars, reference the task id (`P0-02: add append-only triggers`).
- No `Co-Authored-By` or other AI-attribution lines in commits or PR descriptions.
- Commit/push only when the user asks.
