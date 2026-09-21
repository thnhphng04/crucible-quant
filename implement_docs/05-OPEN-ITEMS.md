# 05 — Open items

Implementation-level unknowns. Each names the task that must resolve it. Architecture-level decisions live in the arch §10 register; the only open one there is **D4**.

Close an item by recording the answer (an ADR if it's a decision) and moving the row to "Closed".

| ID | Question | Blocks | Resolve in | Notes |
|---|---|---|---|---|
| O1 | **D4 — economic threshold** (`holdout_pass`) | First holdout opening, live | Before P1-10 is *used* (not built) | Arch §10. P1-10 refuses to open the holdout while it is unset, so building doesn't wait |
| O9 | When to move the ledger from SQLite to Postgres | — | Phase 2 | Trigger: concurrent writers from the async pipeline (§3.1.10) cause lock contention in WAL mode |
| O13 | Module-wise template: how named blocks `entry` / `exit` / `regime` map onto `indicators()` / `signal()` | Module-wise evolution (D16 ≠ `joint`) | Phase 2 | The parser and `evolve_scope` hashing already support named blocks (P0-05); the phase-0 template has one `joint` block |
| O14 | Gate ④ grid = M full NautilusTrader backtests per candidate (seconds each) — too slow for the phase-2 loop | Phase-2 throughput | Phase 2 | Options: parallel sandbox containers; a vectorized coarse backtester for the grid only (must match NautilusTrader on a reference set) |
| O15 | Gate ④ "variants the refinement loop tried" with different code cannot be re-run: `trials` stores `strategy_hash`, not source | Full §3.2 configuration set in the loop | Phase 2 | Needs the source archive of every measured candidate (agent archive); phase 1 uses same-hash parameter variants only (ADR-0011) |
| O16 | The real-data campaign opened in phase 0 predates `derived.sizing`: gate ③ refuses its lock, it is OPEN, and it cannot be closed without opening the holdout | Any real-data phase-1 run | User decision | Options: an `ABANDONED` state that burns the campaign without an opening (audit event; the holdout stays unseen but is spent for this campaign), or re-carving a fresh holdout. Architecture §4.2 has no such state — the user's call (ADR-0018) |

## Closed

| ID | Question | Answer | Where |
|---|---|---|---|
| — | Code layout vs arch §5 (`core/` at root, package `quantcrucible`); holdout code vs data in one folder | src layout; holdout data at root, code in the package | [ADR-0001](adr/0001-src-layout-and-holdout-split.md) |
| O4 | `campaigns` had no column for the lock hash | `lock_hash` + `holdout_lock_hash` columns | [ADR-0002](adr/0002-ledger-and-config-additions.md) |
| O5 | `trials.cluster_id` was written after insert | append-only `clustering_runs` + `trial_clusters`; no table accepts UPDATE | [ADR-0002](adr/0002-ledger-and-config-additions.md) |
| O12 | Gates after ③ had nowhere to write their `GateResult` | append-only `gate_results` table | [ADR-0002](adr/0002-ledger-and-config-additions.md) |
| O7 | Holdout file protection on Windows | read-only attribute + `icacls /deny <user>:(WD,AD,WEA,WA,DE)` — specific rights only: the generic `(W)` right includes SYNCHRONIZE and made the files unreadable (found on the first real carve; test `test_hardened_file_stays_readable`); SHA256 in `holdout.lock` stays the real tamper check | P0-09, ADR-0001 |
| O3 | NautilusTrader version, wheels, trade-through limits, tick slippage | pin `>=1.231,<1.232` (cp312 Windows + manylinux wheels); next-open fills via synthetic open ticks + 1 ns latency; `prob_fill_on_limit=0`; slippage charged as extra taker fee in bps | [ADR-0003](adr/0003-nautilus-fill-semantics.md) |
| O6 | Docker on Windows: mount paths, `--read-only` + tmpfs, startup cost | Docker Desktop (WSL2) mounts `%TEMP%` job folders fine; `--read-only` + a 256 MB `/tmp` tmpfs work; container start ≈ 0.6 s, a typical job 2.5–4 s (NautilusTrader import ≈ 2 s, loaded only by backtest jobs) — fine for phase 0; the long-lived worker stays the phase-2 fallback | [ADR-0004](adr/0004-docker-sandbox.md) |
| O2 | `purgedcv` real API: does DSR take `N` and `V[SR]` separately? What PBO input/selection rule? License/version? | 0.1.6, MIT, pin `<0.2`; DSR takes `N` and `V[SR]` separately but only a returns series — DSR/PSR implemented ourselves from moments, PBO (`(n_configs, n_obs)`, IS-argmax Sharpe) and CPCV wrapped | [ADR-0007](adr/0007-purgedcv-wrap-vs-implement.md) |
| O8 | Leaky oracle level 4 needs point-in-time data | a synthetic report published 3 bars after its event date but joined at the event date; real PIT data matters from phase 4 (equities) | [ADR-0005](adr/0005-leaky-oracles-and-dynamic-guardrail.md) |
| O10 | ONC implementation: own code vs a library; silhouette-based cluster count on thousands of series may be slow | own ONC (scikit-learn k-means) + a significance guard so unrelated trials are never merged; O(n²) fine for hundreds, revisit in phase 2 | [ADR-0009](adr/0009-n-eff-onc-with-significance-guard.md) |
| O11 | `PlaceholderSizer` used in phase 0 before real sizing exists | deleted; `RiskSizer` (execution) over `PositionSizer` (core); sizing constants locked in `derived.sizing` | [ADR-0010](adr/0010-risk-sizing-in-the-execution-path.md) |
