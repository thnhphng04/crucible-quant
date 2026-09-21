# ADR-0002 — Ledger schema and config additions for phase 0

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P0-02 · **Arch:** §3.2, §4.1, §4.2, §10, §10.1 · **Open item:** O4, O5, O12

## Context

Building the ledger exposed gaps in the arch DDL. §10.1 says the lock is hashed into `campaigns`, but the table has no column for it (O4). `trials.cluster_id` is written after the insert by the `N_eff` step, so `trials` could not be strictly append-only (O5). §3.2 requires every `GateResult` in the ledger, but gates after ③ have nowhere to write, because the `trials` row can't be updated (O12). Phase 0 also needs two parameters the arch never set: the MinBTL target Sharpe and the research data universe. Finally, §3.2 says gates run in ascending `cost`, while its own table orders ② (cost 1) after ①b (cost 2).

## Decision

1. **`campaigns`** gains `lock_hash TEXT NOT NULL` and `holdout_lock_hash TEXT`. A campaign must start `OPEN`; only `status` may change, and only `OPEN → FROZEN → BURNED`.
2. **No table accepts UPDATE or DELETE**, enforced by SQLite triggers (only `campaigns.status` moves as above). `trials.cluster_id` is removed; clustering goes to `clustering_runs` + `trial_clusters`. `trial_stats.n_eff` = clusters in the latest run + trials that run didn't cover (each its own cluster); no run ⇒ `n_eff = n_raw`.
3. **`gate_results`** stores every `GateResult`, pass or fail, with `trial_id` once the candidate has one. `trials` gains `candidate_id` to join them. `generation_log` keeps the reject events, with new names `LEAK_REJECT`, `MINBTL_REJECT`, `GATE_ERROR`, `CANDIDATE_SUBMITTED`.
4. **`trials.source`** also accepts `manual` (hand-written strategies, e.g. the phase-0 EMA crossover). Manual trials count toward `N` like any other.
5. **`holdout_access`** may be inserted only while the campaign is `FROZEN` (trigger), and `frozen_at < accessed_at` (CHECK).
6. **Config, Group B:** `minbtl_target_sharpe: 1.5` (D17; values > 1.5 rejected at load — a lower target is stricter) and `data: {exchange, symbols, timeframe, start, holdout_months}` (D18: Binance spot, 1d, BTC/ETH/SOL/BNB/XRP vs USDT from 2018, last 12 months as holdout).
7. **Gate order is the explicit order of the §3.2 table**, not a sort by `cost`.

## Consequences

- The append-only guarantee holds for any client, including raw `sqlite3` (tested).
- Re-clustering keeps history; `N_eff` can be audited over time.
- Arch §3.2, §4.1, §4.2, §10 (D17, D18) and §10.1 were updated in both languages.
- At a MinBTL target of 1.0, ~7 years of free IS data would reject every candidate after ~100–200 trials; 1.5 allows a few thousand (MinBTL ≈ 4.6 years at N = 1000).

## Alternatives considered

- **Trigger allowing UPDATE of `trials.cluster_id` only** — rejected by the user: loses clustering history and weakens "never UPDATE".
- **Write the `trials` row only after the last per-candidate gate** — rejected: a crash between ③ and ④ would lose a trial, biasing `N` downward.
