# ADR-0047 — Volatility clauses and one ATR yardstick (grammar v5)

- **Status:** Accepted
- **Date:** 2026-10-04
- **Task:** P3-61 · **Arch:** §3.1.3, §3.1.11, §3.3.1 r4, ADR-0041 · **Open item:** —

## Context

The grammar has no clause for the volatility regime. P3-54 measured the candidates: a raw band width `4·std/sma` grows with its period (median 0.145 at n = 10, 0.575 at n = 100), so a fixed `k` range is degenerate; divided by √n its level depends on the timeframe and instrument (median 0.05 at 1d, 0.008 at 1h) and tracks ATR/close (ρ 0.74–0.81). A fast/slow ratio of widths per √bar and `atr(fast)/atr(slow)` hold the same range at 1d and 1h (q05–q95 ≈ 0.3–2.7 and 0.4–2.4); the user chose them on 2026-10-04.

Gate ③ (§3.3.1 r4) rejects two features whose bar-to-bar changes correlate above `max_indicator_corr` 0.9. On the IS bars two ATRs of one series correlate 0.80–0.99, so a ratio written as two features would fail it. The same test showed a defect of ADR-0041: ATR(14) for the guard beside ATR(n_stop) for the stop (0.94–0.996) failed 39 of 40 genomes of a `stop_period` lock, at 1d and 1h. No trial ever ran with the gene — ADR-0041 landed after the last campaign opened. The user chose one ATR yardstick.

## Decision

- Registry: `bandwidth(x, n) = 4·std / (sma·√n)`; `band_ratio(x, fast, slow)` and `atr_ratio(bars, fast, slow)`, fast over slow, NaN unless the slow side is > 0. Kinds `series_ratio` and `bars_ratio`; a ratio's warm-up is its slow period's.
- `VolRatio(fast, slow, op, k)` and `Bandwidth(fast, slow, op, k)`: one feature each, rendered `x["fK"] op k` with `"fK": ind.atr_ratio(bars, n_fast, n_slow)` or `ind.band_ratio(bars.close, …)`. Fast ∈ [5, 50] and slow ∈ [60, 300], log-uniform and disjoint; `k` ∈ [0.6, 1.6] and [0.3, 2.5]. Degenerate share at 1d / 1h: 2.7% / 7.9% and 4.1% / 3.5%.
- Grammar v5 (new locks): the nine clause types; `volatility` is a fifth category on either side, with its own island (i4; the open island becomes i5); `FEATURE_MAP_V2` lists it (no campaign opened under v2 before). Versions 1–4 keep seven types and five islands.
- One ATR: an ATR stop with an `n_stop` gene renders `"atr": ind.atr(bars, self.p.n_stop)` as its only ATR, read by the guard, the stop and `k_tp` (`Genome.one_atr`). ADR-0041 kept ATR(14) so tuning the stop would not rescale `Distance`'s `k`; since v4 `Distance` divides by its spread, and a genome with an ATR `Distance` keeps both ATRs. The rule follows from the genome alone, for every version.
- Kernel: `OP_BANDWIDTH`, and `OP_ATR_RATIO` / `OP_BAND_RATIO` with the slow period in `inst_aux`; a ratio clause compiles to `CL_THRESHOLD`; `atr_slot` is ATR(n_stop) under one ATR.

## Consequences

- v5 genomes fail the correlation test 6% of the time (12/200 at 1d, 11/200 at 1h), always on two entry features; no ATR pair is left (INV-121).
- `atr_ratio`'s changes still correlate 0.57–0.89 with an ATR yardstick: a long slow window brings some `VolRatio` genomes close to 0.9.
- CPU, the CUDA simulator and an RTX GPU match the registry and `generate_signals` bit for bit; the router's report equals the sandbox's.
- The sandbox image must be rebuilt: the registry it runs gained three indicators.
- No gate or architecture change: §3.3.1 r4 still counts every feature, and §3.1.3 names categories as examples.

## Alternatives considered

- A squeeze `bw / sma(bw, m)` — 0.80–0.84 correlated with the ratio, and a nested kernel series.
- Two features per ratio — gate ③ rejects most of them.
- Leaving the guard and stop ATRs out of the correlation test — loosens a gate for every strategy.
- A genome flag for the guard — ambiguous once a point move turns the stop into a Bollinger stop.
