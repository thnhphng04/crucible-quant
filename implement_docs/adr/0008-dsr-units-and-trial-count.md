# ADR-0008 — DSR: units, moments and what counts in `N`

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-02 · **Arch:** §3.1.6, §3.2 row ⑤, §4.1, 07-VALIDATION-LAYER §4.3 · **Open item:** —

## Context

The architecture fixes the DSR inputs (`N_eff` + `V[SR]` from `trials`, plus `portfolio_variants`, on the consolidated portfolio) but not their units or conventions. `trials.sharpe_is` is annualized (engine, ADR-0006) while the DSR formula works per observation, and the sample Sharpe, skew and kurtosis each have more than one textbook definition.

## Decision

1. **Per-observation units inside the formula.** `portfolio_dsr` converts the ledger's annualized `V[SR]` with `var_sr / periods_per_year` (exact, since annual SR = per-obs SR · √periods).
2. **Moments follow `purgedcv`** (ADR-0007): SR with the population standard deviation (`ddof=0`), bias-corrected skew, bias-corrected non-excess kurtosis; a test pins PSR and DSR equal to `purgedcv` to 1e-12.
3. **`N` = trials + portfolio variants**, for both counts: `n_eff = trial_stats.n_eff + n_variants`, `n_raw = trial_stats.n_raw + n_variants`. `trial_stats` spans every campaign, engine and verdict.
4. **Both counts are always reported** (`DsrReport.dsr_n_eff`, `.dsr_n_raw`, with their benchmarks). Which one gate ⑤ compares with `dsr_min` is decided in P1-08.
5. **No per-strategy/per-cell entry point.** The public functions take moments or one portfolio return series; `portfolio_dsr` is the only function that reads `TrialStats` (INV-42).

## Consequences

- INV-40 enforced: `test_dsr_reference_example` reproduces Bailey & López de Prado (2014): SR₀ ≈ 1.7894 annualized, DSR ≈ 0.9004.
- `validation/statistical.py` now imports `ledger.records` (allowed: validation sits above the ledger).
- A negative `var_sr` from SQL float noise is clamped to 0.

## Alternatives considered

- **Store per-observation Sharpe in `trials`:** rejected — would change the phase-0 schema and the public metric the prompts see.
- **Sample standard deviation (`ddof=1`) as in the engine's Sharpe:** rejected — the difference is O(1/T), and matching `purgedcv` keeps the cross-check exact.
- **Variants only added to `N_raw`:** rejected — a portfolio variant is a selection whatever the clustering says.
