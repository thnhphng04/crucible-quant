# ADR-0017 — Calibration: every evaluation is a trial

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-11 · **Arch:** §3.2.1 step 5b, §3.3.1 (optimizer off in the loop), §4.1 ("hidden trials") · **Open item:** —

## Context

Step 5b runs Optuna once, before the freeze, for each selected strategy with a fixed budget; every evaluation must be a `trials` row with `source='param_opt'`, and PBO and DSR must be recomputed afterwards. Open: which gates an evaluation passes through, how the calibrated strategy replaces the original in the portfolio, and how the "no optimizer in the loop" rule is enforced.

## Decision

1. **`optuna>=4.5,<5`** (MIT; its dependencies are MIT/BSD, tqdm MPL-2.0 + MIT). TPE sampler seeded; integer TUNABLEs sampled as integers, within the declared bounds.
2. **Each evaluation** runs `GatePipeline([InSampleGate()])` — straight to gate ③ — with `candidate_id = <original>-optNNN` and `trial_source='param_opt'`. Skipping ①a/①b/② is deliberate: the code is the original's, already checked; running ② per evaluation could reject one *before* it is measured and so hide a trial. Evaluations rejected at ③ are still trials and score −10.
3. **Confirmation:** the best passing parameters go through the full candidate pipeline ①a → ④ **under the original `candidate_id`** (one more `param_opt` trial). Gate ④ recomputes PBO with the calibration rows among its ledger variants (same `strategy_hash`). Because eligibility takes each candidate's latest trial and requires *that* trial to have passed ④ (ADR-0013 amendment), the calibrated trial replaces the original at the next build — or the candidate drops out if the confirmation fails ③ or ④; there is no fallback to the original trial. That build's gate ⑤ recomputes DSR.
4. **Enforcement:** `tests/test_architecture_boundaries.py::test_parameter_optimizer_only_in_calibration` — optuna (and hyperopt, skopt, nevergrad, ray) may be imported only by `validation/calibration.py`.

## Consequences

- INV-44: `tests/validation/test_calibration.py::test_every_eval_is_a_trial` (budget B ⇒ B + 1 `param_opt` rows).
- Calibration raises `N` by B + 1 per strategy; with the default budget of 50 and ~20 members, ~1 000 trials — DSR pays for it, as §3.2.1 intends.
- The orchestration (calibrate each member, rebuild, re-run ⑤–⑥′) is wired in P1-12.

## Alternatives considered

- **Evaluations through the full pipeline:** rejected — ② could hide a measured configuration; ④ per evaluation would cost M backtests each.
- **Calibrated strategy under a new candidate id:** rejected — original and calibrated versions would compete in the portfolio as two strategies.

## Amendment — review of d765668

- The confirmation re-uses the original `candidate_id`, so it used to overwrite the original trial's returns file (finding 1). Artifacts are now written once per measurement (ADR-0013 amendment); the original trial's returns stay as measured.
- **Policy when the confirmation fails:** the candidate leaves the portfolio at the next build. Falling back to the pre-calibration trial would select, after the fact, whichever of the two measurements looks better — a hidden selection step.

## Amendment — first real-data run (2026-09-22)

- **Attempts, errors, trials.** The calibration of RSI momentum made 50 Optuna attempts: 47 were measured at ③ (47 `param_opt` trials) and 3 failed inside the sandbox before anything was measured (`stop_distance = NaN` during the indicator warm-up). So "budget B ⇒ B + 1 rows" holds only when no attempt fails: B attempts = measured trials + errors, plus one confirmation trial. `CalibrationResult` now lists every `Attempt` (id, params, outcome `passed` / `rejected` / `error`, trial id, Sharpe, reason), and one `CALIBRATION_FINISHED` audit event records all B of them with the three counts.
- **No made-up Sharpe.** An error has no returns and no Sharpe, so it has no `trials` row (§4.1 requires both). Optuna gets `FAILED_SCORE` to steer its search; that number is never stored as a Sharpe.
- **Traceability of the real run** (it predates the event): all 50 attempt ids `…-opt000` to `…-opt049` are in `gate_results` at ③ — 47 with a trial id, 3 without and with the error — and in 50 `CANDIDATE_SUBMITTED` audit events.
- **Effect on N and V[SR], measured, not assumed.** V[SR] cannot include an error (there is no value). N does not include it today (§4.1). Counting the 3 errors in N, each as its own cluster, would move the two variants of that campaign as follows: RSI `778e…` DSR 0.999 → 0.998 at N_eff (4 → 7), 0.986 → 0.985 at N_raw; SMA `d7bd…` **0.963 → 0.936** at N_eff — below `dsr_min` — and 0.792 → 0.788 at N_raw. The counting rule can therefore flip gate ⑤; the counting convention was decided in [ADR-0022](0022-counting-attempts-that-measured-nothing.md) (O17): not counted, reported as a sensitivity.
- Tests: `tests/validation/test_calibration.py::test_every_attempt_is_accounted_for`.

## Amendment 2 — review of b8dc07a

- **Once, with the locked budget, enforced by the ledger** (MEDIUM). Nothing stopped a second `portfolio --calibrate`: with budget 1 a rerun took N_raw from 2 to 4 and reused attempt id `x-opt000` — more search after seeing results. Schema v4 adds `calibration_runs` (campaign, candidate, strategy hash, budget; one per candidate and one per strategy code in a campaign; OPEN campaigns only) and `calibration_finishes` (outcome `finished` or `stopped`, attempts ≤ budget, trials, errors), both append-only. A run is registered before its first attempt, with a `CALIBRATION_STARTED` audit event; `calibrate_members` checks every member before any of them runs.
- **Technical retry vs further optimization — one rule.** A registered run may run again only if it never finished *and* recorded no attempt result (nothing was measured, so nothing was seen): same candidate, code and budget, flagged `technical_retry` in the audit event. Anything else is refused (`CalibrationRefused`): a finished or stopped run, another budget, or an interrupted run with recorded attempts — resuming it would search beyond the budget after results were seen; a human decides.
- **Earlier runs carried over.** The migration registers runs recorded before v4 from their `CALIBRATION_FINISHED` event or, older, from their rows (trials of run `calib-<id>`, one ③ result per attempt). The real campaign's RSI calibration becomes a finished run of 50 attempts (47 trials + 3 errors); N is unchanged (checked on a copy of the ledger).
- **Search finished ≠ calibration succeeded** (user decision). The budget is locked as soon as the search ends: Optuna never runs again for that strategy. The confirmation is tracked on its own (`CALIBRATION_CONFIRMATION`: `passed`, `rejected`, `not_run` when no attempt passed ③, `error`; no event after a start means `incomplete`), and a refusal or an error says "budget used; confirmation failed or incomplete — calibration did NOT succeed" instead of "already calibrated". Re-running only the confirmation is refused for now; if it is supported later, it measures the stored parameters only and every measurement is a trial by the usual rule. A confirmation that died after ③ keeps its ③ trial in `N`.
- Tests: `tests/validation/test_calibration.py::test_a_rerun_reports_the_confirmation`, `::test_a_crashed_confirmation_is_not_reported_as_success`, `::test_a_failed_confirmation_says_so`, `::test_a_second_calibration_is_refused`, `::test_the_same_code_under_another_id_is_refused`, `::test_a_technical_retry_is_allowed_only_if_nothing_was_measured`, `::test_an_interrupted_search_is_not_resumed`, `::test_a_stopped_calibration_is_not_rerun`; `tests/ledger/test_db.py::test_calibration_runs_once_per_strategy`, `::test_v3_calibrations_are_carried_over`, `::test_older_calibrations_are_carried_over_from_their_rows`.
