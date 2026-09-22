# ADR-0013 — Portfolio construction and the strategy archive

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-07 · **Arch:** §3.2.1 steps 1–6, D9, §3.1.6, §4.1 (`portfolio_variants`) · **Open item:** O15 (partly)

## Context

§3.2.1 fixes the rule (cell representative by DSR-rank, |ρ| filter, cap K, naive risk parity, scheduled rebalance, hash + variant row) but leaves open: what "DSR-rank" is when DSR may only be computed on the portfolio (§3.1.6), which trial represents a candidate, how returns are combined, what goes into the hash, and when a rebuild is a new variant. Later stages (⑥′, holdout) must also re-run the members, yet the ledger keeps only `strategy_hash`.

## Decision

1. **Eligible** = the latest trial of every candidate, if *that trial* passed gate ④ (`gate_results.trial_id`); a later failed measurement takes the candidate out (amended below).
2. **Selection score** ("DSR-rank") = PSR of the trial's IS returns against the *campaign-wide* deflated benchmark SR₀(`N_eff`, `V[SR]`) — one benchmark for all, so it is a ranking that penalizes short, skewed or fat-tailed records; it is never compared with `dsr_min` (no per-strategy DSR gate exists).
3. **Steps:** one representative per `cell_id` (no cell ⇒ its own cell); |ρ| of IS returns on common dates vs every accepted member, in score order (NaN ⇒ dropped); cap `max_strategies`; weights w ∝ 1/σ (sum 1, no leverage); holdings reset to the weights at every `rebalance` boundary and drift in between.
4. **`portfolio_hash`** = SHA256 of canonical JSON: members sorted by `strategy_hash` with params and weight (rounded to 1e-10) + the rule (`max_corr`, `max_strategies`, `rebalance`, selection, weighting).
5. **Variants:** each distinct hash is one `portfolio_variants` row with its IS returns under `results/portfolios/`; rebuilding an identical portfolio is not a new selection and is not recorded again.
6. **Strategy archive** (`validation/archive.py`): `GatePipeline.run` stores every submitted source at `results/strategies/<strategy_hash>.py`; reads re-verify the hash. This also removes the source blocker of O15 for the phase-2 loop.

## Consequences

- INV-45: `test_rule_change_is_new_variant`; each step has its own test in `tests/validation/test_portfolio.py`.
- New ledger reads: `portfolio_variants()`, `holdout_access()`, `gate_result_details()`.
- The selection score depends on `trial_stats` at build time; rebuilding after more trials may reorder — and is then a new variant, counted in `N`.

## Alternatives considered

- **Rank by raw IS Sharpe:** rejected — ignores track length and higher moments that DSR corrects for.
- **Per-strategy DSR vs `dsr_min` as a filter:** rejected — §3.1.6 forbids DSR per cell/strategy.
- **Store source in the ledger:** rejected for now — a schema change; the archive keeps the ledger schema and verifies content by hash.

## Amendment — review of d765668

- **Eligibility is bound to the trial** (finding 2). Before: "passed ④ at some point" was paired with "the latest trial", so a later trial rejected at ③ (which has no ④ result) could be selected. Now a candidate's latest measurement decides; if it failed ③ or ④ — e.g. a failed calibration confirmation — the candidate is out, with no fallback to an older pass. Tests: `tests/validation/test_portfolio.py::test_a_later_rejected_trial_is_not_eligible`, `::test_the_4_pass_must_belong_to_the_selected_trial`.
- **Measurement artifacts are immutable** (finding 1). Gate-③ returns and the gate-④ matrix were stored under `<candidate_id>.parquet`, so re-measuring the same id rewrote the returns an earlier `trials` row points to (corrupting history, `N_eff` clustering and re-checks of older portfolios). Each measurement now gets a fresh path, created exclusively (`validation/artifacts.py`). Tests: `tests/validation/test_is_gates.py::test_g3_artifacts_are_immutable_per_measurement`, `tests/validation/test_pbo_gate.py::test_g4_matrix_is_immutable_per_measurement`.

## Amendment — review of b8dc07a

- **The members' window is theirs alone** (HIGH). The common window was taken over every representative before the correlation filter, and the members' weights and returns were computed on it: adding a candidate the filter then dropped — 100 days of history against the member's 400 — cut the portfolio to 100 days and moved its Sharpe (4.569 → 5.054), while its hash, which covers members, weights and rule only, stayed the same and the stored artifact kept 400 days. Now ρ is pairwise, on each pair's own shared days (`min_periods = 30`; with fewer, ρ is not measurable and the lower-ranked candidate is dropped), and weights and returns are computed on the chosen members' common window only.
- **Same hash, same data.** When a build's hash is already recorded, its returns must equal the stored artifact or the build is refused, so gates ⑤/⑥′ never evaluate data other than the variant's artifact. The reviewer found the two real variants unaffected (artifact and member trials: 2,819 rows each).
- Tests: `tests/validation/test_portfolio.py::test_a_dropped_candidate_does_not_change_the_portfolio`, `::test_member_weights_use_the_members_window_only`, `::test_candidates_with_too_little_overlap_are_not_accepted`, `::test_a_recorded_hash_must_match_its_artifact`.
