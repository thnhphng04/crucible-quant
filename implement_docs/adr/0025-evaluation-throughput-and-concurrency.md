# ADR-0025 — Evaluation throughput: measured cost, cheaper windows, concurrent slots

- **Status:** Accepted
- **Date:** 2026-09-22
- **Task:** P2-08 · **Arch:** §3.1.8, §3.2 ④, P5 · **Open item:** narrows O14, updates O9

## Context

Gate ④ backtests every configuration of the PBO grid (`M` ≤ 200). Engine C sends hundreds to thousands of candidates through it, so its cost decides whether a phase-2 campaign fits in days or weeks. Measured on the development machine (i7-12700H, 20 threads, Docker Desktop with 8 GB), synthetic data shaped like the campaign (5 symbols × 2,450 daily bars), one CPU per container: gate ③ ≈ 12.7 s; ≈ 9 s per grid configuration; PBO + CPCV on a 200-configuration matrix < 1 s. A profile showed ~45% of a backtest spent rebuilding the bar window from Python tuples and building pandas objects in the Risk layer on every bar.

## Decision

1. **Cheaper windows, same results.** The bridge keeps OHLCV and close times in growing numpy buffers; a bar's window is a view of the last `lookback` rows. The Risk layer stores raw arrays and builds pandas only when it rebalances; the monthly/quarterly period key comes from numpy. Equity curves are bit-for-bit identical to before (monthly and weekly rebalancing); one backtest drops from 8.9 s to 5.4 s on the host.
2. **Concurrent evaluation slots.** The pipeline runs `workers` slots; each has its own ledger connection (WAL + `busy_timeout = 30 s`). Measured throughput with 10-configuration grid jobs: 6 slots ≈ 0.89 backtests/s, 12 slots ≈ 1.08 backtests/s — the machine saturates near one backtest per second. Peak memory ≈ 270 MB per container, so 12 slots fit in 8 GB. Default: `workers = 8`.
3. **Labelled containers, killed on abort.** Every sandbox container carries `quantcrucible.run=<label>`; the pipeline's abort path calls `SandboxRunner.kill_all()` (INV-69).
4. **Budget planning:** at `M = 200`, a candidate that reaches ④ costs ≈ 200 s of machine time. P2-15 sizes `trial_budget` with this figure.

## Consequences

- O14 narrowed: throughput is known and ~40% better; the remaining lever is `pbo_grid.max_configs` (D13, the user's call) — no vectorized backtester (P5).
- O9 stays open: no lock errors with 6 concurrent writers in tests; revisit if a real run shows contention.
- Indicators are still recomputed on the whole window every bar (≈ 20% of a backtest). That is the live path (P5); caching them would need its own ADR.

## Alternatives considered

- **Vectorized grid backtester:** rejected — a second backtest engine breaks P5.
- **Long-lived worker container:** not needed yet — container start-up is small next to ≈ 5–9 s backtests.
