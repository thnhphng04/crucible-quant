# ADR-0011 — Gate ④: configuration set and a vectorized CSCV

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-03 · **Arch:** §3.2 (④, "What PBO actually tests"), D13, §3.3.2 · **Open item:** O14, O15 (opened)

## Context

§3.2 defines gate ④'s configuration set (TUNABLE grid ±30% + the variants the loop actually tried, `M ≤ 200`) and selection rule (highest IS Sharpe), rejecting at PBO ≥ `pbo_max`. Open: whether grid configurations are trials, how the grid is run, and the cost of CSCV. ADR-0007 planned to wrap `purgedcv`'s PBO, but it evaluates the metric per configuration per slice in Python: S = 16 (12 870 combinations) on a 125 × 2 800 matrix took 85 s — per candidate.

## Decision

1. **Grid configurations are not trials** (user decision, 21 Sep 2026): they never replace the candidate's parameters. Their return matrix is saved to `results/pbo/<campaign>/<candidate>.parquet` (+ `.configs.json`) and referenced from `gate_results.detail`; `trials` gets nothing.
2. **Configuration set** = candidate params, then every param set of the **same `strategy_hash`** already in this campaign's `trials` (e.g. calibration rows), then `pbo_grid(..., center=candidate.params)` — deduplicated, capped at `max_configs`, candidate always first. The grid is centered on the candidate's actual params, not the declared defaults. Fewer than 2 configurations ⇒ reject (fail closed).
3. **One sandbox job** `grid_backtest` runs every configuration with the same data, costs and sizing as gate ③ (`backtest_options` from the lock) and returns the `(M × T)` matrix. Job timeout `max(300 s, 30 s × M)` (new `SandboxJob.timeout_s`).
4. **Own vectorized CSCV** in `validation/pbo.py` (amends ADR-0007 §3): per-block sums and sums of squares → Sharpe for all combinations at once. Same definitions as `purgedcv` (ddof = 1, degenerate slice ⇒ 0, first argmax, average ranks, ω = rank/(M+1)); a test pins PBO and logits equal to `purgedcv` on random matrices. S = 16 on 200 × 2 800 takes ~0.6 s. `n_splits` is written to the lock (`derived.pbo.n_splits = 16`).
5. **PBO, slope, M are private** (`EvaluationReport.private` + `gate_results`), never `public` or feedback.
6. `candidate_pipeline()` = ①a → ①b → ② → ③ → ④; `run_candidate` still defaults to the phase-0 pipeline until P1-12 switches the CLI.

## Consequences

- INV-41: `test_pbo_reference_example` (hand-computed from the paper's definition, S = 2), `test_pbo_noise` (≈ 0.5), `test_pbo_planted_edge` (→ 0), plus `test_matches_purgedcv`.
- **O14:** the grid job costs M full backtests (~seconds each on NautilusTrader) — tolerable for hand-written phase-1 strategies, too slow for the phase-2 loop.
- **O15:** "variants the refinement loop tried" with *different code* cannot be re-run: the ledger stores `strategy_hash`, not source. Needs the phase-2 source archive.

## Alternatives considered

- **Wrap `purgedcv` PBO:** rejected on speed (85 s at S = 16); kept as the test oracle.
- **S = 10:** rejected — 16 is the paper's standard; speed is solved by vectorizing.
- **Grid configs as trials (`source='pbo_grid'`):** rejected by the user — would multiply `N_raw` ~100× per candidate for configurations nobody selected.
