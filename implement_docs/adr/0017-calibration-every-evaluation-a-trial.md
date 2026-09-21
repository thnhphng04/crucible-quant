# ADR-0017 — Calibration: every evaluation is a trial

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-11 · **Arch:** §3.2.1 step 5b, §3.3.1 (optimizer off in the loop), §4.1 ("hidden trials") · **Open item:** —

## Context

Step 5b runs Optuna once, before the freeze, for each selected strategy with a fixed budget; every evaluation must be a `trials` row with `source='param_opt'`, and PBO and DSR must be recomputed afterwards. Open: which gates an evaluation passes through, how the calibrated strategy replaces the original in the portfolio, and how the "no optimizer in the loop" rule is enforced.

## Decision

1. **`optuna>=4.5,<5`** (MIT; its dependencies are MIT/BSD, tqdm MPL-2.0 + MIT). TPE sampler seeded; integer TUNABLEs sampled as integers, within the declared bounds.
2. **Each evaluation** runs `GatePipeline([InSampleGate()])` — straight to gate ③ — with `candidate_id = <original>-optNNN` and `trial_source='param_opt'`. Skipping ①a/①b/② is deliberate: the code is the original's, already checked; running ② per evaluation could reject one *before* it is measured and so hide a trial. Evaluations rejected at ③ are still trials and score −10.
3. **Confirmation:** the best passing parameters go through the full candidate pipeline ①a → ④ **under the original `candidate_id`** (one more `param_opt` trial). Gate ④ recomputes PBO with the calibration rows among its ledger variants (same `strategy_hash`). Because portfolio eligibility takes each candidate's latest gate-④ result and latest trial, the calibrated trial replaces the original at the next build — or the candidate drops out if the recomputed PBO fails; that build's gate ⑤ recomputes DSR.
4. **Enforcement:** `tests/test_architecture_boundaries.py::test_parameter_optimizer_only_in_calibration` — optuna (and hyperopt, skopt, nevergrad, ray) may be imported only by `validation/calibration.py`.

## Consequences

- INV-44: `tests/validation/test_calibration.py::test_every_eval_is_a_trial` (budget B ⇒ B + 1 `param_opt` rows).
- Calibration raises `N` by B + 1 per strategy; with the default budget of 50 and ~20 members, ~1 000 trials — DSR pays for it, as §3.2.1 intends.
- The orchestration (calibrate each member, rebuild, re-run ⑤–⑥′) is wired in P1-12.

## Alternatives considered

- **Evaluations through the full pipeline:** rejected — ② could hide a measured configuration; ④ per evaluation would cost M backtests each.
- **Calibrated strategy under a new candidate id:** rejected — original and calibrated versions would compete in the portfolio as two strategies.
