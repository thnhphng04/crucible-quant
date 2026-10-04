# ADR-0043 — Strategy categories by direction (grammar v2)

- **Status:** Accepted
- **Date:** 2026-10-04
- **Task:** P3-56 · **Arch:** §3.1.3, §3.1.5, §3.1.11 · **Open item:** —

## Context

`clause_category` labelled a clause on its own. Since P3-12 every scope has a side, and on the short side the labels flip: `rsi > 70` sold is mean reversion, a `Breakout(down)` sold is a breakout. Some long labels were already loose: `close < sma(200)` was "trend", and `Distance(a, b, >, k)` and `Distance(b, a, <, −k)` — one condition — got different labels. The category bits are a feature-map dimension and the islands are seeded by category, so both carried the error.

## Decision

- New locks record `derived.grammar_version: 2`; a lock without the key is version 1 (`lock_grammar_version`).
- v2: a clause has a family — trend (`Compare`, `Cross`, `Distance`, price `Slope`), momentum (`Threshold`, `CrossLevel`, oscillator `Slope`), breakout (`Breakout`) — and a polarity, +1 when it holds as price rises. Two-series clauses are read fast − slow (close, then shorter periods, `ema` before `sma` at one period). Category = family if polarity × side > 0, else mean reversion.
- `clause_category(c, direction, version)` and `categories(genome, direction, version)`; version 1 returns the old labels.
- The pipeline records `descriptors.categories` with the scope's side (long for a legacy key) and the lock's version; the archive keeps reading the recorded labels, so trials already in the ledger keep theirs.
- Under v2 a category island seeds clauses of its category only: `CATEGORY_CLAUSES_V2` (every type for mean reversion), redrawn up to 50 times. C-random and the open island are unchanged.

## Consequences

- A short scope's islands and feature-map bits mean what their names say (INV-117).
- Golden renders, sampling under v1 and the comparison protocol hash are unchanged; the open campaigns resume.
- Later grammar changes (P3-57 …) raise `GRAMMAR_VERSION` and branch on `>= N`.
- No arch edit: §3.1.3 names the categories but not how a clause gets one.

## Alternatives considered

- Rename the categories `long` / `short` — the side is already its own dimension (the scope); the category is the reason for the entry.
- Mirror only the short side and keep the v1 rules — keeps the loose long labels and the two labels of one `Distance` condition.
