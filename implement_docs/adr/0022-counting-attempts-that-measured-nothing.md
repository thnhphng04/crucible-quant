# ADR-0022 — Counting convention: attempts that measured nothing

- **Status:** Accepted (decided by the user)
- **Date:** 2026-09-22
- **Task:** after the first real-data run · **Arch:** §4.1 (trials = measured configurations), §3.2.1 5b · **Open item:** closes O17

## Context

Three of the 50 calibration attempts on RSI momentum failed in the sandbox before anything was measured (ADR-0017 amendment). Counting them in `N`, each as its own cluster, would move SMA's gate ⑤ from 0.963 to 0.936 — below `dsr_min`. The question was whether such attempts belong in `N`.

## Decision

1. **Not a trial:** an attempt that failed before producing any performance output — no returns, no Sharpe, nothing a user or the optimizer could select on — is not a statistical trial. It is not in `N`, not in V[SR], and never given a Sharpe of 0 or any other made-up value.
2. **Measured, then rejected, still counts:** a configuration whose performance was measured is a trial whatever gate rejects it afterwards (unchanged).
3. **Errors after output are classified separately:** an attempt that exposed a performance number but recorded no measurement is `error_after_output`. It is never exempted automatically: calibration stops and a human decides how to count it.
4. **Traced, always:** every attempt is in `gate_results` and in the `CALIBRATION_FINISHED` audit event.
5. **Sensitivity, reported, not decisive:** gates ⑤ and ⑥′ also report the DSR they would give if each unmeasured ③ attempt (ledger-wide) were one more trial in its own cluster. Nothing supports treating the three warm-up errors as three independent tests, so this stays a check, not the rule.
6. **A convention, not a proof:** Optuna receives `FAILED_SCORE` for a failed attempt, so a failure does steer the next suggestions. Excluding it from `N` is the project's counting convention; it does not claim the failure had no influence on selection.

## Consequences

- The real run stands as recorded: N_raw = 52 trials; the three warm-up errors are not added.
- Tests: `tests/validation/test_portfolio_dsr.py::test_unmeasured_attempts_are_a_sensitivity_not_a_count`, `tests/validation/test_calibration.py::test_an_error_after_output_is_not_silently_exempt`, `::test_every_attempt_is_accounted_for`.

## Alternatives considered

- **Count every attempt in `N`:** rejected — an attempt with no output offers nothing to select, and treating each as an independent test has no basis.
- **Exempt every error:** rejected — an error raised after a number was shown could hide a selection.
