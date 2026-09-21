# ADR-0012 — CPCV for rule-based strategies

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-04 · **Arch:** §3.2 (④, IS→OOS degradation curve), 07-VALIDATION-LAYER §3, §4.1, §3.3.2 · **Open item:** —

## Context

CPCV (López de Prado, AFML ch. 12) fits a model on each fold's purged training set and predicts the test set. Our strategies are deterministic rules: nothing is fitted, so CPCV on one fixed configuration would just slice its IS return series. What *is* fitted is the choice of parameters. The architecture wants CPCV-OOS median Sharpe as a private metric and as the OOS line of the degradation curve, without the holdout.

## Decision

1. **Selection is the "fit":** in every fold, the configuration with the best training-set Sharpe is chosen from gate ④'s `(M × T)` matrix (the loop's own rule, same as PBO) and its returns on the test blocks are kept. No extra backtests.
2. **Splits** from `purgedcv.CombinatorialPurgedCV` (N = 6 groups, k = 2 ⇒ 15 folds, 5 paths; 07 §4.1): observation *i* = the return of bar *i*; its label spans `horizon_bars` = ⌈candidate's average holding period⌉ (≥ 1); training rows whose span overlaps a test span are purged (both sides); embargo = ⌊1% · T⌋ rows after each test block. Paths assembled with `purgedcv.reconstruct_paths`.
3. **Private metrics** from gate ④: `cpcv_oos_median_sharpe`, `cpcv_oos_negative_share`, `cpcv_n_paths` (annualized Sharpe); per-path Sharpes and per-fold selections in `gate_results.detail`.
4. **Degradation curve:** `is_oos_diverging(points)` flags IS record rising (OLS slope > 0) while the same strategies' CPCV-OOS median is flat or falling, over ≥ 3 checkpoints. Recording checkpoints per engine and stopping an engine is phase-2 loop work.
5. A fold with no training data left after purge/embargo raises ⇒ gate ④ fails closed.

## Consequences

- INV-47: `test_oos_curve_is_private`; purge/embargo pinned on a hand-built 12-bar fixture (`test_purge_removes_rows_whose_label_overlaps_the_test_block`, `test_embargo_drops_rows_after_each_test_block`).
- CPCV adds no sandbox time; it reuses the grid matrix. Its quality depends on the configuration set (O15).

## Alternatives considered

- **CPCV on the candidate's single configuration:** rejected — equals slicing IS returns; measures nothing about selection.
- **Walk-forward only:** rejected — one OOS path, the problem CPCV exists to solve (07 §4.1).
- **Purge only before the test block:** rejected — overlapping labels after the block leak too (AFML §7.4.1).
