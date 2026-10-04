# ADR-0044 — Log-uniform periods and oscillator period caps (grammar v3)

- **Status:** Accepted
- **Date:** 2026-10-04
- **Task:** P3-57 · **Arch:** §3.1.11, §3.3.1 r2 · **Open item:** —

## Context

P3-54 measured 18.8% of sampled clauses degenerate — true on < 1% or > 99% of in-sample bars — mostly from three causes. Periods drawn uniform on [2, 300] fall above 100 two times in three, where two moving averages rarely cross. A long RSI hugs 50, so its levels in [50, 90] or [10, 50] are seldom reached. A z-score of n values never exceeds (n − 1)/√n — 1.79 at n = 5 — so its ±2.25 and ±3 levels are unreachable. Log-uniform periods plus RSI ≤ 30 and z-score ≥ 10 brought it to 10.9% on the in-sample bars, the same on the second exchange.

## Decision

- New locks record `derived.grammar_version: 3`.
- v3 draws every entry period log-uniform within its bounds: `round(exp(U(ln lo, ln hi)))`. Bounds are unchanged, so `param` moves and the gate-④ grid still span them.
- v3 caps oscillator periods: `rsi` ∈ [2, 30], `zscore` ∈ [10, 300] (`PERIOD_CAPS_V3`); the caps become the TUNABLE bounds.
- `point` swapping `rsi` ↔ `zscore` under v3 moves the period into the new oscillator's range and bounds.
- `n_stop` keeps its uniform [5, 50]; levels and `k` keep their ranges. Below v3 nothing changes: the golden renders and the v1/v2 streams hold.
- `Cross` and `CrossLevel` periods are not capped: they are events, rare in bars but frequent in count on short timeframes, and campaigns move to 1h.

## Consequences

- Fewer dead clauses and trials (INV-118): on a seeded random walk 17.6% → 12.0%, `Cross` 42% → 20%, `CrossLevel` 47% → 22%.
- `Distance` gets worse (7.5% → 17.5%): log-uniform periods favour short ones, where ATR(14) is too wide a yardstick. P3-58 changes its denominator; no campaign opens in between.
- No arch edit: the ranges are implementation constants, declared as TUNABLE bounds (§3.3.1 r2).

## Alternatives considered

- Narrower bounds instead of log-uniform draws — `param` and the PBO grid would lose the long periods altogether.
- Level ranges that depend on the RSI period — couples two genes; a moved period would need its level redrawn.
