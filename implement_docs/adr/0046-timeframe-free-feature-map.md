# ADR-0046 — A timeframe-free feature map (v2)

- **Status:** Accepted
- **Date:** 2026-10-04
- **Task:** P3-59 · **Arch:** §3.1.3 · **Open item:** —

## Context

The feature map's bounds were set for daily bars: `trades_per_year ∈ [0, 150]` cut linearly, so on 1h bars every strategy trading more than 150 times a year would share the top bin, and `total_return ∈ [−1, 4]`, which grows with the IS length. Read against the 2,473 gate-③ trials of the 1d campaigns, two more bounds were too wide for risk-at-the-stop sizing: 98% of drawdowns lie under 0.36 of `[0, 1]`, and total returns span barely three of the sixteen bins. Campaigns move to 1h (2026-10-04), possibly shorter later; the grammar keeps its periods in bars, so only the map's measures needed to become timeframe-free.

## Decision

- New locks record `FEATURE_MAP_V2`: it names its `dimensions` and its `log` ones. A lock without `dimensions` is v1 and is read exactly as before.
- `trades_per_year ∈ [1, 10 000]` on a log scale: each of 16 bins spans a factor 1.78; zero trades fall in bin 0.
- `annual_return` — the compound rate `(1 + total)^(1/years) − 1` — replaces `total_return`, bounds `[−0.1, 0.3]`.
- `max_drawdown ∈ [0, 0.5]`; `sharpe_is` and `sortino_is` keep `[−1, 3]` and `[−1.5, 4.5]`.

## Consequences

- One map serves 1d and 1h campaigns and any IS length (INV-120); bounds stay fixed per campaign (INV-63).
- The bounds are provisional: the first 1h campaign's trials are the next calibration, for the campaign after it.
- No arch edit: §3.1.3 lists the dimensions (frequency, drawdown, Sharpe, Sortino, return) without fixing their units.

## Alternatives considered

- Bounds per timeframe — one table per timeframe to maintain, and cells that mean different things across campaigns.
- Percentile bins learnt from the trials — stretches with observed values, which §3.1.3 forbids.
