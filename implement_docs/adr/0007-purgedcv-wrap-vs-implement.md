# ADR-0007 — `purgedcv`: what we wrap and what we implement

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-01 · **Arch:** §6 note, §3.2, §4.1, 07-VALIDATION-LAYER §7 · **Open item:** O2 (closed)

## Context

The architecture names `purgedcv` as the core of the validation layer but flags its API as unverified (§6, §10): nobody had checked whether DSR takes `N` and `V[SR]` separately, what PBO's input and selection rule are, or the license. The fallback was to implement DSR/PBO ourselves. P1-01 installed `purgedcv` 0.1.6 and read the source (`_metrics.py`, `_pbo.py`, `_cpcv.py`, `_paths.py`, `_purge.py`, `_embargo.py`).

## Decision

1. **Dependency:** `purgedcv>=0.1.6,<0.2` (MIT, ships `py.typed`). It pulls scikit-learn, joblib, threadpoolctl, cloudpickle (BSD-3) and narwhals (MIT) — no GPL/AGPL.
2. **Checked signatures (0.1.6):**
   - `deflated_sharpe_ratio(returns, n_trials, var_sharpe, *, bars_per_year=None) -> float` — **`N` and `V[SR]` are separate inputs**; `var_sharpe` is per-observation unless `bars_per_year` is given. SR uses `ddof=0`, skew/kurtosis are bias-corrected, kurtosis is non-excess. It takes a returns series only, never moments.
   - `probability_of_backtest_overfitting(returns, n_splits=16, *, metric=sharpe, prediction_times=None, evaluation_times=None, purge_horizon=None, embargo=…) -> PBOResult` — input matrix is **`(n_configs, n_obs)`** (the transpose of the paper's T×N); selection rule = **argmax of `metric` in IS** (default Sharpe, as §3.2 requires); OOS rank ω = rank/(M+1); PBO = share of logits < 0. Optional purge/embargo via `CombinatorialPurgedCV`.
   - `CombinatorialPurgedCV(n_splits, n_test_groups, *, prediction_times, evaluation_times, purge_horizon, embargo | embargo_observations | embargo_fraction).split(X)` yields purged + embargoed `(train_idx, test_idx)`; `reconstruct_paths(...)` builds the C(N−1,K−1) OOS paths. `backtest_paths` needs an sklearn estimator — not used.
3. **What we do with each:**
   - **DSR / PSR — implement ourselves** in `validation/statistical.py` from moments (`sr, sr_benchmark, n_obs, skew, kurt`), with the benchmark `√V[SR] · expected_max_sharpe(N)` (existing function). Reason: the published example (Bailey & López de Prado 2014) is stated in moments, which `purgedcv` cannot take; and ⑤ must report DSR at both `N_raw` and `N_eff`. `purgedcv.deflated_sharpe_ratio` is kept as a **cross-check in tests** (probe: equal to 1e-15 on a random series).
   - **PBO — wrap** `probability_of_backtest_overfitting` in `validation/pbo.py` (P1-03), `S = 16`.
   - **CPCV — wrap** `CombinatorialPurgedCV.split` + `reconstruct_paths` in `validation/cpcv.py` (P1-04); per-fold selection over the grid matrix is ours.
   - **MinBTL — keep ours** (ADR-0006); a test pins it equal to `purgedcv.minimum_backtest_length`.
   - **Not used:** `effective_n_trials` (an autocorrelation heuristic over trial order) — `N_eff` is ONC clustering per §4.1 (P1-05).
4. **Only the wrapper modules** (`validation/statistical.py`, `validation/pbo.py`, `validation/cpcv.py`) may import `purgedcv`; enforced by `tests/test_architecture_boundaries.py`.

## Consequences

- O2 closed. P1-02, P1-03, P1-04 are unblocked; the probe reproduced the paper's DSR example (0.9004) from moments.
- The sandbox image grows by scikit-learn (installed from `uv.lock`); P1-05 may reuse scikit-learn for KMeans/silhouette.
- `purgedcv` is alpha (0.x): the `<0.2` pin plus the cross-check tests catch a silent change of formula.
- No architecture change. 07-VALIDATION-LAYER §7 now marks these signatures as verified (user-approved, VI first, then EN).

## Alternatives considered

- **Wrap DSR too:** rejected — cannot be tested against the paper's moment-based example, and hides the SR/moment conventions we must control.
- **Implement PBO/CPCV ourselves:** rejected — the library's CSCV and purge/embargo match the paper and are tested; ~300 lines saved.
- **`effective_n_trials` as `N_eff`:** rejected — depends on trial order, not return correlation; §4.1 specifies ONC.
