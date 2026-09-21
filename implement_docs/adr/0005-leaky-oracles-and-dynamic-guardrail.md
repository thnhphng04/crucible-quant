# ADR-0005 — Leaky oracles and the dynamic guardrail (①b)

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P0-11 · **Arch:** §3.2 (①a, ①b), §7 phase 0, 07-VALIDATION-LAYER §9 · **Open item:** O8

## Context

The phase-0 gate is that the pipeline rejects four leaky oracles: `.shift(-1)`, the close of a forming candle, a repainting indicator, and information joined before it was published. The roadmap expected level 1 to die at ①a and levels 2–4 at ①b. Two facts changed that picture:

- The ①a whitelist (P0-07) allows only registered causal indicators (INV-33), arithmetic, comparisons and `Signal`. No leak can be written inside it: every oracle needs an import, a slice or a non-whitelisted call, so **all four die at ①a**.
- Every strategy runs through `step()` on a window that ends at the current bar, so a look-ahead inside `indicators()` cannot reach the future in the backtest itself; it only makes the signals inconsistent.

①b still matters: it is the second line of defence if the whitelist ever has a hole (a non-causal indicator added to the registry, a data join done wrong).

## Decision

1. **Oracles** live in `validation/oracles/level{1..4}_*.py`, in template form with an intact fixed region (so ①a reports `AST_REJECT`, not `TEMPLATE_TAMPER`):
   1. tomorrow's close via `pd.Series.shift(-1)`;
   2. a 2-day candle, grouped by calendar day, whose final close is stamped on both of its bars;
   3. a centred 5-bar moving average (`np.convolve`, mode `same`);
   4. a synthetic report about day *t* that is published 3 bars later but joined at *t* (O8: free crypto data has no point-in-time history, so the delay is synthetic).
2. **①b measurement** (`validation/leak_check.py`, sandbox job `leak_check`): for 20 seeded cut points *t* in `[n/10, n−2]`, the signals at the 20 bars `s ≤ t` are computed from indicators on the full series, on `bars[:t+1]` (truncation), and on a copy whose bars after *t* are a seeded random walk with the pre-*t* volatility (perturbation). Any difference (direction, or strength / stop / take-profit beyond 1e-9 relative) is a leak. Indicators start from the first bar in all three runs, so recursive filters (EMA) match exactly.
3. **Gate ①b** (`DynamicGuardrail`) runs that job in the sandbox on the candidate's IS universe; one leak ⇒ `LEAK_REJECT` with the first 10 leaks in `detail`. A sandbox failure goes through `sandbox_failure()` (ADR-0004).
4. **①a reports what a call chain is built on:** a non-whitelisted call is also checked inside, so `pd.Series(x).shift(-1).to_numpy()` is reported as a `.shift()` look-ahead, not only as a forbidden call.
5. **Acceptance, restated:** the full pipeline rejects all four oracles at ①a with ledger rows; a pipeline with ①b alone (①a removed, simulating a hole) rejects all four at ①b with `LEAK_REJECT` rows; the EMA crossover passes ①a and ①b.

## Consequences

- INV-34 is proven twice: `tests/validation/test_oracles.py` (the host-side measurement across 5 seeds and 2 series, plus docker-marked gate and pipeline tests).
- ①b costs one sandbox job (≈ 3 s) plus 41 indicator passes per symbol.
- Detection is statistical for level 2 (only the first bar of each 2-day group leaks); 20 cut points × 20 bars catch it on every tested seed.

## Alternatives considered

- **Loosen ①a so levels 2–4 reach ①b in the full pipeline:** rejected — gate thresholds and guardrails may only be tightened.
- **Re-run only the last bar with its close perturbed (the roadmap's level-2 idea):** rejected — the signal at a closed bar legitimately depends on that bar's close; perturbing bars *after* the cut is what isolates the future.
- **Compare against `step()` on a lookback window:** rejected — recursive indicators started at different bars differ slightly and would raise false alarms.
