# ADR-0006 — Gates ② MinBTL and ③ in-sample backtest

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P0-12 · **Arch:** §3.2 (②, ③), §3.3.1 rule 4, §4.1, D15, D17 · **Open item:** —

## Context

Gates ② and ③ close phase 0. The architecture fixes what they check (MinBTL from the running `N`; minimum trades, minimum holding time and maximum indicator correlation at ③; a `trials` row from ③ on) but not which `N`, which history length, how "indicator correlation" is measured, where the costs come from, or how the phase-0 pipeline is started outside tests.

## Decision

1. **MinBTL** (`validation/statistical.py`): Bailey, Borwein, López de Prado & Zhu (2014), `E[max_N] = (1−γ)Z⁻¹[1−1/N] + γZ⁻¹[1−1/(Ne)]`, `MinBTL = (E[max_N] / target)²` years; 0 for `N ≤ 1`. Tests pin the paper's examples (N = 7 → ≈ 2 years, N = 45 → ≈ 5 years at target 1).
2. **Gate ②** uses `N = trial_stats.n_eff + 1` (every trial so far, all campaigns, N_raw until clustering runs, plus this candidate) and the target Sharpe from the lock (`minbtl_target_sharpe`, D17). The available length is the span of the **shortest** series in the candidate's universe.
3. **Gate ③** runs a `backtest` job in the sandbox with the costs and lookback recorded in the lock's `derived` section at campaign open (`CostModel` defaults, lookback 400). Its public metrics become the `EvaluationReport`; per-bar returns go to `results/returns/<campaign>/<candidate>.parquet` and the path into the `trials` row. The row is written whatever the verdict; a crash in the sandbox measures nothing and writes no row (its `gate_results` row records it).
4. **Indicator correlation is measured on bar-to-bar changes**, not levels, over every pair of series returned by `indicators()`, per symbol, and the maximum |ρ| is compared with `max_indicator_corr`. The levels of any two price-following indicators correlate near 1 (EMA20 vs EMA100 on BTC: 0.99), which would reject every multi-indicator strategy without saying anything about redundancy; their changes do not (0.75).
5. **P2 by structure:** an import-linter contract forbids `quantcrucible.agent` from importing `execution`, the sandbox or the leak check directly, so the agent can reach a backtest only through `GatePipeline`, which writes the ledger. `execution` stays free of the ledger so the live path equals the backtest path (P5).
6. **`quantcrucible.cli validate FILE`** runs one strategy through ①a → ①b → ② → ③. It resumes the campaign behind `config/evaluation.lock.yaml` (after `assert_lock_matches`), or opens one: the holdout range and hash come from `holdout.lock`, and the IS bars from `ResearchStore`, which refuses the holdout range.

## Consequences

- The phase-0 gate is covered end to end by `tests/e2e/test_phase0.py` (docker, slow): the four oracles are rejected at ①a (and at ①b when ①a is bypassed) without becoming trials; the EMA crossover passes all four gates and is trial #1.
- The first real run (2026-09-21, Binance daily IS data, 2018-01 → 2025-09) opened campaign `c-20260921-104445`; the EMA crossover passed with IS Sharpe 1.07 over 206 trades. Profitability is not a phase-0 criterion.
- INV-01's enforcement now reads "pipeline + import contract", not "the runner requires a ledger handle".

## Alternatives considered

- **Pass a ledger handle into `run_backtest`:** rejected — it would tie the live execution path to the research ledger (P5).
- **Correlation of levels:** rejected (see 4). **Spearman on levels:** same problem.
- **Longest series or the union span for ②:** rejected — the shortest series is the conservative choice.
