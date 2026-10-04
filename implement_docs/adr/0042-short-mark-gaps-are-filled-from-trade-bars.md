# ADR-0042 — Short mark gaps are filled from the same minute's trade bar

- **Status:** Accepted
- **Date:** 2026-10-04
- **Task:** P3-24 · **Arch:** §6.1, D18 · **Narrows:** INV-94 (ADR-0032) · **Open item:** —

## Context

The first real perpetual fetch (2026-10-04) stopped on `BTC/USDT:USDT mark: missing minute in download page`. The minute store refuses every hole (P3-17, INV-94), and the venue's one-minute **mark** history has holes. A survey of the 2020-09-15 → 2026-10-02 window, with every archive hole re-checked against the live API, found:

- 24 minutes missing on 2020-12-17 07:32–07:55, on all five contracts;
- 1–7 minutes missing on 2022-07-12/13 for ETH, SOL, BNB and XRP (at most 11 per contract);
- 2 minutes missing on 2024-08-12 10:02 for SOL and XRP.

That is at most 35 minutes per contract out of ~3.18 million. **Trade** minutes are complete on all five contracts, and nothing is missing in the holdout window. Whole-day holes in the data.binance.vision archives are archive defects: the API serves those days. Without a rule, no perpetual campaign can open on this venue.

## Decision

1. **A missing mark minute is filled from the trade bar of the same minute:** open, high, low and close are copied, and volume is set to 0. A fill is allowed only if the run of consecutive missing minutes is at most `MAX_MARK_FILL_MINUTES = 60` and the trade store holds every minute in the run.
2. **Anything else is still refused:**
   - a longer run;
   - a trade gap (trade minutes are never filled);
   - a hole with no trade minute;
   - a duplicate, step-back or off-grid page;
   - a hole after the last served minute.
3. **Each fill is recorded:**
   - in `<contract>_1m.mark.fills.json` beside the minute cache, written before the page that holds the fill, so a resume repeats a fill rather than losing its record;
   - in `PerpCoverage.mark_fills`;
   - in `mark_fills.json` in the IS bundle directory, which `manifest.json` checksums. That file lists in-sample minutes only, so it says nothing about the holdout window.
4. Funding settlement marks and the liquidation paths read the filled minutes like any other.
5. **A settlement belongs to its nearest minute** (`settlement_minute_ms`). The same fetch then stopped on `no completed one-minute mark before funding settlement 2020-09-15 08:00`: Binance stamps settlements a few milliseconds off the boundary. The replay and the kernel already place a settlement with `round((ts - open) / 1 min)`. The funding-coverage check, the bar and mark lookup, the path cuts (which silently dropped every off-minute stamp) and the preflight now use the same rule. A funding row keeps the venue's own stamp.

## Consequences

- The real fetch can complete. On the measured window a liquidation test uses the trade price for at most 35 minutes per contract over five years.
- The narrower INV-94 still holds: a missing mark or funding *series* fails closed and nothing is zero-filled. The exception has its own invariant, INV-116; settlement placement is INV-117.
- A mark gap the API does not show today could appear later. It is either filled under the same cap and recorded, or refused.
- No architecture edit is needed. §6.1 asks for mark and funding and does not specify gap handling.

## Alternatives considered

- **Forward-fill the last mark.** Rejected: the price would be up to 24 minutes stale.
- **Mark the affected bars as unresolvable** (no entry; worst outcome for a held position). Rejected: it would change the CPU and GPU replays for about 35 minutes of data.
- **Start the IS window after the last gap (2024-08-12).** Rejected: about 1.1 years of in-sample data leaves MinBTL no headroom.
