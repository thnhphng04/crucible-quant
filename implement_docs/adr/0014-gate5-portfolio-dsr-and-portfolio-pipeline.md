# ADR-0014 — Gate ⑤: portfolio DSR and the portfolio pipeline

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-08 · **Arch:** §3.2 rows ⑤–⑥′, §3.1.6, §4.1 ("Inputs to gate ⑤ DSR", "Estimating `N_eff`") · **Open item:** —

## Context

From ⑤ on the object under test is a portfolio, which the candidate `GatePipeline` cannot carry (it writes `trials` rows and audit events keyed on a strategy). §4.1 fixes gate ⑤'s inputs (`trial_stats` + `total_portfolio_variants`, applied to the consolidated portfolio's returns) and says `N_eff` is re-estimated on every portfolio evaluation and DSR is always reported at both `N_raw` and `N_eff` — but not which of the two is compared with `dsr_min`.

## Decision

1. **`PortfolioPipeline`** (`validation/portfolio_dsr.py`): gates in the order ⑤ → ⑥′, stop at the first failure, an exception is a rejection, and every result goes to `gate_results` with `candidate_id = portfolio_hash` (no `trials` row, no audit event — a portfolio is not a trial; its selection is counted through `portfolio_variants`).
2. **Gate ⑤** first runs `update_n_eff` (a new clustering run over every trial), then `portfolio_dsr(returns, trial_stats, total_portfolio_variants, periods_per_year)` (ADR-0008).
3. **Pass iff DSR at `N_eff` ≥ `dsr_min`.** `N_eff` is the principled count (ONC + the significance guard, ADR-0009, which never merges unrelated trials). DSR at `N_raw` and both benchmarks are in `gate_results.detail` for every evaluation.
4. `deflated_sharpe_ratio` is called from `statistical.py` only — a test scans the source tree.

## Consequences

- INV-42: `tests/validation/test_portfolio_dsr.py::test_more_trials_lower_dsr` (200 more trials from another engine, half of them rejected ⇒ lower DSR at both counts), `::test_no_per_cell_dsr_api`.
- ONC is bounded at k ≤ 50 and 3 initializations (ADR-0009 amended) so gate ⑤ stays in seconds with hundreds of trials.

## Alternatives considered

- **Compare DSR at `N_raw`:** rejected as the default — §4.1 allows `N_eff` to lower the bar on principled grounds, and the guard keeps that principled; `N_raw` stays reported. Tightening to `N_raw` would be a §10 decision.
- **Reuse `GatePipeline` with a fake candidate:** rejected — it would write a `trials` row for the portfolio and inflate `N`.

## Amendment — review of d765668

- Every ⑤/⑥′ result also stores `ledger_snapshot` — the ledger-wide counts of trials and portfolio variants when the run started (`Ledger.snapshot()`). DSR falls as either grows, so a result is current only while the snapshot is unchanged; the freeze checks it (ADR-0016 amendment, finding 3).
- **Both counts, side by side** (2026-09-22): the pass rule is unchanged (DSR at `N_eff` ≥ `dsr_min`), but every ⑤ result — and ⑥′'s stressed DSR — now reads `DSR a at N_eff=x, b at N_raw=y [N_eff = k clusters of m trials + u unclustered + v variants; N_eff/N_raw = r]`, and `detail` carries the same counts. On the real run N_eff = 4 against N_raw = 54 (r = 0.07); a gap that large must be visible where the result is read. Test: `tests/validation/test_portfolio_dsr.py::test_both_counts_and_the_clusters_are_reported`.
