# ADR-0018 — Phase-1 workflow, CLI and end-to-end test

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-12 · **Arch:** §3.2, §3.2.1, §4.2, §7 phase 1 · **Open item:** O16 (opened)

## Context

P1-01…P1-11 built each gate and step on its own. The phase gate (§7) asks for the whole path: a hand-written strategy through every gate, a portfolio through ⑤ → ⑥′, calibration, the freeze and one holdout opening, with the ledger separating audit from trials and `N_eff` + `V[SR]` computable. Open: where the orchestration lives, what the CLI exposes, and what data the end-to-end test runs on.

## Decision

1. **`validation/research_run.py`** holds the pre-freeze workflow: `ResearchSession` (ledger, lock, IS data of both sources, sandbox, results dir → `GateContext`), `submit` (①a → ④), `evaluate_portfolio` (build by the locked rule, a variant row if new, then ⑤ → ⑥′), `calibrate_members` (5b for each member from its archived source, only if the lock enables it) and `run_phase1` (submit all → build → calibrate → rebuild). It adds no gate logic and no side path: every backtest still goes through a pipeline and the ledger (P2).
2. **Zoo:** `core/zoo/sma_trend.py` (price over SMA) and `core/zoo/rsi_momentum.py` (RSI above a level) join `ema_crossover.py` — valid template instances with the same fixed region (INV-30 test covers all three).
3. **CLI:** `validate FILE` now runs ①a → ④ (`candidate_pipeline`); `portfolio [--calibrate]` builds and runs ⑤ → ⑥′ (refuses without second-source data from `data-fetch-second`); `freeze HASH` moves OPEN → FROZEN. The CLI never imports `quantcrucible.holdout`; the holdout is opened only by `python -m quantcrucible.holdout.evaluator_proc --portfolio HASH` (P6).
4. **End-to-end test** `tests/e2e/test_phase1.py` (docker, slow) runs on **synthetic data only**: a regime-switching trending market (`make_trending_bars`, so a trend follower *can* pass), a second "vendor" printing the same market with 0.1 % price noise (`second_vendor`), and a synthetic holdout carved in a tmp dir. The grid is kept small (3 values per parameter, 10 configurations) and the calibration budget is 3, for run time only; every threshold is the locked default.

## Consequences

- Phase-1 gate tests: `test_ema_crossover_clears_every_candidate_gate`, `test_portfolio_clears_5_and_6p` (DSR at `N_eff` ≥ 0.95, `N_raw` reported), `test_ledger_separates_audit_from_trials`, `test_calibration_rows_and_new_variant`, `test_freeze_then_one_holdout_opening` (one token, campaign BURNED).
- Passing on synthetic data shows the harness *lets an edge through*; it says nothing about a real market. No real-data phase-1 run has been made.
- **O16:** the campaign opened on real data in phase 0 predates `derived.sizing`, so gate ③ refuses its lock; it cannot be closed without opening the holdout, and no "abandon" transition exists. A real-data phase-1 run needs the user's decision (see 05).

## Alternatives considered

- **Orchestration inside the CLI:** rejected — the e2e test would then exercise argument parsing rather than the workflow, and phase 2's agent loop needs the same functions.
- **E2E on the real research data:** rejected — slow, needs the network, and whether a hand-written strategy passes on a real market is a research result, not a harness property.
