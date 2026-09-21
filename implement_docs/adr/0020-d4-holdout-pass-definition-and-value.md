# ADR-0020 — D4: what `holdout_pass` measures, and its value

- **Status:** Accepted (decided by the user)
- **Date:** 2026-09-21
- **Task:** before the next campaign · **Arch:** §10 D4, §4.2 step 3, §10.1 · **Open item:** closes O1 (the holdout part of D4)

## Context

The holdout evaluator compares one number with `research.holdout_pass` and prints PASS or FAIL (ADR-0016). Until now D4 was open and ADR-0016 only *assumed* the number was a Sharpe ratio. The threshold is Group B, so it is locked with a campaign and must be fixed before that campaign's research starts.

## Decision

1. **Definition:** `holdout_pass` is the minimum **annualized out-of-sample Sharpe ratio of the whole frozen portfolio**, measured on the holdout, **net of fees and slippage**, with a **reference return of 0**. PASS iff that Sharpe ≥ `holdout_pass`.
2. **As computed** (`holdout/evaluator_proc.py`, unchanged): every member re-run in the sandbox with the campaign's locked `derived.costs`; combined with the frozen weights and rebalance rule; only bars closing inside the holdout range; `mean / std(ddof=1) · √(periods per year)` (365 for 1d crypto).
3. **Value: 1.3** for the next campaign, set in `config/user.yaml`. The user chose it as an initial acceptance bar; it is **not** a value shown to be right by the synthetic tests (their 0.5 was a fixture). The schema default stays `None`, so a config that omits the key never opens a campaign.
4. **Locked:** written into the campaign lock when the campaign opens; a change mid-campaign is refused, and the evaluator reads the lock, not `user.yaml`. It is never adjusted after seeing a holdout result — a used holdout is never reused (ADR-0016 amendment), so a different threshold can only apply to a later campaign on new data.
5. **Meaning of PASS:** the frozen portfolio cleared this bar on the holdout — nothing more. It is **not** approval to trade real money; the funding criteria for live trading remain open in D4 and need their own decision before live.

## Consequences

- Tests: `tests/holdout/test_evaluator.py::test_d4_is_the_frozen_portfolios_annualized_sharpe` (a hand-computed Sharpe from known member returns; the jobs carry the locked, non-zero costs), `::test_d4_pass_means_at_least_the_threshold`, `::test_d4_comes_from_the_campaign_lock_not_user_yaml`; `tests/config/test_lock.py::test_holdout_pass_is_locked_for_the_whole_campaign`.
- The phase-0 real campaign's lock has `holdout_pass: null`, so with 1.3 in `user.yaml` every command on it is refused as a Group B change — abandon it (ADR-0019) and the next command opens a new campaign with 1.3 locked.
- A 12-month holdout is about 365 daily returns: the standard error of an annualized Sharpe is roughly 1, so an edge near 1.3 passes by luck about as often as it fails. The bar filters; it does not prove.

## Alternatives considered

- **Leave D4 open:** not possible — a new campaign now requires `holdout_pass` (ADR-0019).
- **Measure gross of costs, or against a risk-free rate:** rejected by the user's definition — net of costs, reference 0.
