# ADR-0027 — Phase-2 comparison: protocol locked in the ledger, budget, decision

- **Status:** Accepted
- **Date:** 2026-09-23
- **Task:** P2-15 · **Arch:** §3.1.11 (comparison, decision rule), §7 phase 2, D14 · **Open item:** —

## Context

§3.1.11 (v0.6) compares C-gp with C-random in `isolated` mode — same data, gates and trial quota, ≥ 3 seeds — with the decision rule locked before running. It names five metrics but leaves open which one decides, what "beats … by more than the seed-to-seed spread" means numerically, how "locked before running" is enforced, and the trial budget.

## Decision

1. **Protocol** (`agent/compare.py::PROTOCOL`, versioned, hashed). Primary metric: **trial efficiency** = strategies passing gate ④ per 100 trials, per (engine, seed). C-gp *beats* C-random when `mean(gp) − mean(random) > max(sd(gp), sd(random))` (sample sd across seeds). Both below 1 per 100 ⇒ *neither meaningful*: stop and find the cause. Otherwise *tie* ⇒ C-random is the main engine (§3.1.11). Supporting, not decisive: archive and search coverage, proposals per trial, gate-④ backtests per passing strategy, IS→OOS divergence, and the DSR of a §3.2.1 portfolio built from each engine alone at the same `N` (recorded as a variant, like every built portfolio). Never the best strategy's IS Sharpe.
2. **Locked in the ledger:** `cli compare --lock` writes `PROTOCOL_LOCKED` (protocol + SHA-256) to the campaign's audit log, only while the campaign has no trial. `evolve` refuses a harness-test campaign without it; the report refuses a campaign where any submission precedes it (INV-70).
3. **Budget** (`config/user.yaml` for the run): `campaign: {purpose: harness_test, trial_budget: 600}`, engines gp 0.5 / random 0.5, 3 seeds ⇒ 100 trials per (engine, seed). MinBTL allows ~37,000 trials over 7.7 IS years at target Sharpe 1.5; machine time binds instead: ≈ 1 backtest/s (ADR-0025) ⇒ ≈ 10–15 h if ~30–40% of trials reach ④ at `M` ≤ 200.
4. The phase-2 campaign's strategies never enter a portfolio that is frozen (the campaign cannot freeze, INV-62).

## Consequences

- The comparison adds 600 trials and two portfolio variants to `N` for every later campaign — the price of an honest control (§3.1.11: parallel runs give no free trials).
- A different metric or spread after seeing results would need a new protocol version and a new campaign.

## Alternatives considered

- **A t-test on seed means:** rejected for now — with 3 seeds it has little power and hides the arch's plain "beyond the spread" rule.
- **Protocol only in an ADR file:** rejected — a file cannot prove it predates the run; the ledger's append order can.
