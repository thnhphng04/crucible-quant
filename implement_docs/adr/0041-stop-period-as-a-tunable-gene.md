# ADR-0041 — Stop period as a TUNABLE gene

- **Status:** Accepted
- **Date:** 2026-10-02
- **Task:** P3-53 · **Arch:** §3.2, §3.3.1 r2, §10 D24 · **Open item:** —

## Context

Engine C evolves only the stop's width `k_stop`. Its period is hard-coded: ATR(14) for an ATR stop and Bollinger(8, 2) for a Bollinger stop (ADR-0036), both whitelisted outside the ≤ 6 TUNABLE budget by §3.3.1 r2. The renderer, the genome parser, gate ①a, the engine router and the GPU program all assume those two constants. The user decided (D24) that the search may tune how fast the stop reacts, without changing what an entry rule means and without changing any existing campaign.

## Decision

- A new lock records `derived.stop_period: tunable_v1` and `derived.max_tunables: 7`. A lock without the keys means fixed periods and a cap of 6.
- `Genome` gains `stop_period: Param | None = None`, rendered as the TUNABLE `n_stop` ∈ [5, 50] right after `k_stop`. `None` renders byte for byte as before, so old `strategy_hash`es do not move.
- ATR stop: a separate feature `"atr_stop": ind.atr(bars, self.p.n_stop)` and `stop = k_stop * x["atr_stop"]`. `atr` = ATR(14) stays the yardstick of the `ready` guard and of `Distance`.
- Bollinger stop: the band period is `self.p.n_stop`; the 2σ multiplier stays fixed, since `k_stop` already scales the width.
- Gate ①a enforces the lock's cap; under `tunable_v1` the literal 8 is no longer allowed. The parser's hard ceiling becomes 7.
- C-gp and C-random both sample the gene under a `tunable_v1` lock. `param` moves it, and a crossover that takes the mate's stop takes `stop_period` too. The complexity penalty keeps its fixed scale `n_params / 6`: every TUNABLE costs the same in every campaign, and the comparison protocol locked by open `harness_test` campaigns does not change.
- The parser, the CPU engine router and the GPU program (a `stop_slot` beside `atr_slot`) read the gene; CPU ≡ GPU stays bit-exact.

## Consequences

- Each genome carries one more TUNABLE: the PBO grid sweeps `n_stop`, and `n_params` and the cap count it, so the extra freedom is measured rather than free.
- Old campaigns are unaffected: same renders, same cap, same ranking scale on resume (INV-114). New campaigns: INV-115.
- Arch edits (done with this ADR): §3.3.1 r2, §3.1.10 L2, §3.2 configuration set, D24, changes 0.10 → 0.11.
- `STOP_PERIOD_RANGE = (5, 50)` is provisional; changing it is a new lock version, never a mid-campaign edit.

## Alternatives considered

- Keep a cap of 6 and let `n_stop` share it — leaves entry rules one parameter short; rejected by the user.
- One ATR(n_stop) for the guard, `Distance` and the stop — tuning the stop would silently rescale entry thresholds and mix entry and exit effects in the PBO grid.
- Also tune the Bollinger σ multiplier — two genes for one stop, partly redundant with `k_stop`.
