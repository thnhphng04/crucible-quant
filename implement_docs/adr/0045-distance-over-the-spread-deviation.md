# ADR-0045 — Distance over the spread's own deviation (grammar v4)

- **Status:** Accepted
- **Date:** 2026-10-04
- **Task:** P3-58 · **Arch:** §3.1.11, §3.3.1 r3, ADR-0038 · **Open item:** —

## Context

`Distance` divides `a − b` by ATR(14), a one-bar range, while its operands reach 300 bars. P3-54 measured the misfit both ways: on long periods the ratio sits outside `k ∈ [−3, 3]` on 38% of bars, on short ones `|k| ≥ 2` is never reached. Of four denominators measured on in-sample bars, the spread's own standard deviation left 1.6% of `Distance` clauses degenerate, against 6% for ATR(14) (16% once periods are log-uniform), 18% for `stdev(close, n)` and 65% for `ATR(14)·√n`. The user chose it on 2026-10-04.

## Decision

- New registry indicator `spread_stdev(x, y, n)`: the population deviation of `x − y` over n bars; NaN where it is 0 or a window holds a NaN. Kind `pair`, unit price; causal like every entry.
- `Distance.scale ∈ {atr, spread}`, default `atr`, omitted from the JSON form at its default. A `spread` Distance renders `(a − b) / x["fK"]` with `"fK": ind.spread_stdev(<a>, <b>, self.p.<n of b>)` — n of a when b is the close; no new TUNABLE. The parser recovers it; `k = 0` still means `a > b`.
- Grammar v4 (new locks): every sampled `Distance` is `spread`, its operand periods ≤ 200 so the warm-up `max(n_a, n_b) + n − 1` fits the locked 400-bar lookback.
- Kernel: `OP_SPREAD` instances carry their operands in `Program.inst_aux`; one lane rebuilds both operands on its window (ema seeded at the window start), keeps the last n + 1 spreads in a per-lane buffer and takes numpy's pairwise mean and deviation. Clause rows gain a sixth column, the `Distance` yardstick slot.

## Consequences

- `Distance` is live on every period (INV-119): degenerate share 17.5% → 0% on a seeded random walk; CPU, the CUDA simulator and an RTX GPU match the registry and `generate_signals` bit for bit.
- The yardstick's bar-to-bar changes correlate ≤ 0.54 with any other feature on the in-sample bars, well under `max_indicator_corr` 0.9.
- Gate ①a needs no change: `spread_stdev` is whitelisted, the nested calls are allowed, units divide price by price.
- No arch edit: §3.3.1 r3 allows any whitelisted `ind.*`; the whitelist lives in the registry.

## Alternatives considered

- `stdev(close, n)` — MA spreads are far smaller than price dispersion, so `|k| ≥ 2` is unreachable.
- `ATR(14)·√n` — needs a non-integer power in the DSL and fails worse on trends.
- A longer lookback instead of operands ≤ 200 — changes every indicator's window and costs every lane.
