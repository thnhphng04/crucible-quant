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

## Amendment — protocol v2 (2026-09-23)

The first run of v1 exposed two holes, both found before any result was read.

1. **Completeness was implicit.** `decide` ran on whatever the ledger held, so a campaign that had evaluated 3 of 600 trials — all of them GP's — reported `gp_beats_random`. v2 adds `complete`: every (engine, seed) of both arms must have used its whole quota, and with `>= 3` seeds, or the report shows progress under the outcome `incomplete` and withholds the decision. An arm the monitor stopped for IS→OOS divergence gives `stopped_early`: the divergence is the finding, and no engine decision comes out of that campaign.
2. **The locked hash did not bind.** The report recomputed the hash from the code and never compared it with the campaign's `PROTOCOL_LOCKED` event, so editing `PROTOCOL` silently re-interpreted an older campaign. The report and `evolve` now refuse a campaign whose locked hash differs from the code's (`assert_protocol_is_the_locked_one`), and `lock_protocol` refuses to lock a second, different protocol.

Both fixes only tighten the rule, but v2 changes the hash, so campaign `c-20260922-181850` (locked under v1) cannot run under it. Its 121 trials stay in `N`, as every trial does, and the comparison starts again in a new campaign. Two further defects fixed in the same pass affected what the run measured: the evaluation slots were spent on the first (engine, seed) alone (INV-71), and the two arms' portfolio DSRs were computed at different variant counts because each was recorded before the next was built — both arms are now deflated against one snapshot.
