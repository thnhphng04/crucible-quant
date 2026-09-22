# ADR-0021 — Proposal: re-evaluate an earlier portfolio variant on the current ledger

- **Status:** Proposed — for review, **not implemented**
- **Date:** 2026-09-22
- **Task:** after the first real-data run · **Arch:** §3.2.1, §4.1, §4.2 step 2 · **Open item:** —

## Context

In campaign `c-20260921-171247` the first portfolio variant (`778e…`, RSI momentum alone) passed ⑤ and ⑥′. Calibration then added 48 trials; the calibrated RSI failed ④ (PBO 0.549), so by the "latest measurement decides, no fallback" rule (ADR-0013 amendment) RSI left the portfolio, and the rebuilt variant (SMA alone) failed ⑥′. The `778e…` results are stale (ADR-0016 amendment, INV-52) and nothing can re-run ⑤ → ⑥′ on a variant the current rule no longer builds. The user kept the campaign OPEN (option a) and asked for this as a separate proposal before any change.

## Decision (proposed)

1. A command `portfolio-reevaluate HASH` re-runs ⑤ → ⑥′ on a **recorded** variant of the OPEN campaign, with the current `trial_stats` and variant count, and records the results with the current `ledger_snapshot`. It adds no variant row and changes no member, weight or rule.
2. Freeze stays as it is: only the latest ⑤/⑥′ results computed on the current ledger count, so a re-evaluated variant becomes freezable only if it passes on today's `N`.
3. **Guard against a selection loop:** every re-evaluation is logged (`VARIANT_REEVALUATED`) and counted as one more selection in `N` (like a portfolio variant), so trying several old variants until one passes is paid for in DSR. Optionally, at most one re-evaluated variant per campaign.

## Consequences

- The RSI variant would be judged on everything tried since, which is what DSR is for: at today's ledger its DSR is 0.999 at N_eff = 4 and 0.986 at N_raw = 54 (read-only check, 2026-09-22) — ⑥′ would have to be re-run too.
- It weakens the "latest measurement decides" rule for portfolios: an earlier variant can come back after a later one failed. That is exactly the reviewer's concern in finding 2, one level up; point 3 is what makes it pay.

## Alternatives considered

- **Keep the current rule (chosen for now):** the campaign stays OPEN; diversity must come from new, less correlated strategies.
- **Re-evaluate without counting it:** rejected — selection among variants after seeing results would be free, a hidden trial.
