# 05 — Open items

Implementation-level unknowns. Each names the task that must resolve it. Architecture-level decisions live in the arch §10 register; the only open one there is **D4**.

Close an item by recording the answer (an ADR if it's a decision) and moving the row to "Closed".

| ID | Question | Blocks | Resolve in | Notes |
|---|---|---|---|---|
| O1 | **D4 — economic threshold** (`holdout_pass`) | First holdout opening, live | Before P1-10 is *used* (not built) | Arch §10. P1-10 refuses to open the holdout while it is unset, so building doesn't wait |
| O2 | `purgedcv` real API: does DSR take `N` and `V[SR]` separately? What PBO input/selection rule? License/version? | P1-02, P1-03, P1-04 | P1-01 | Arch §6 note. Fallback: implement DSR/PBO ourselves (~50 lines each) |
| O3 | NautilusTrader: version to pin; Windows + Python 3.12 wheels; can `FillModel` express "fill only on trade-through" and fixed-tick slippage? | P0-10 | P0-10 (spike first) | If `FillModel` can't, subclass or post-process fills — record in an ADR |
| O6 | Docker on Windows: Docker Desktop + WSL2 mount paths, `--read-only` + tmpfs behaviour, startup cost per evaluation (thousands of runs) | P0-08 | P0-08 | If per-run startup is too slow: a long-lived sandbox container per worker with a fresh tmp dir per job — keep network/env/mount limits identical |
| O7 | Holdout file protection on Windows (no `chmod 0400`) | P0-09 | ADR-0001 (approach), P0-09 (implement) | Read-only attribute + `icacls` deny-write for the user; the SHA256 in `holdout.lock` is the real tamper check |
| O8 | Leaky oracle level 4 (late-published information) needs point-in-time data; free crypto data has none | P0-11 | P0-11 | Use a synthetic feature with a known publication delay; real PIT data matters from phase 4 (equities) |
| O9 | When to move the ledger from SQLite to Postgres | — | Phase 2 | Trigger: concurrent writers from the async pipeline (§3.1.10) cause lock contention in WAL mode |
| O10 | ONC implementation: own code vs a library; silhouette-based cluster count on thousands of series may be slow | P1-05 | P1-05 | Start from López de Prado's published ONC algorithm; cache the correlation matrix incrementally |
| O11 | `PlaceholderSizer` used in phase 0 before real sizing exists | — | P1-06 | Must be deleted by P1-06; a grep in P1-06's review |

## Closed

| ID | Question | Answer | Where |
|---|---|---|---|
| — | Code layout vs arch §5 (`core/` at root, package `quantcrucible`); holdout code vs data in one folder | src layout; holdout data at root, code in the package | [ADR-0001](adr/0001-src-layout-and-holdout-split.md) |
| O4 | `campaigns` had no column for the lock hash | `lock_hash` + `holdout_lock_hash` columns | [ADR-0002](adr/0002-ledger-and-config-additions.md) |
| O5 | `trials.cluster_id` was written after insert | append-only `clustering_runs` + `trial_clusters`; no table accepts UPDATE | [ADR-0002](adr/0002-ledger-and-config-additions.md) |
| O12 | Gates after ③ had nowhere to write their `GateResult` | append-only `gate_results` table | [ADR-0002](adr/0002-ledger-and-config-additions.md) |
