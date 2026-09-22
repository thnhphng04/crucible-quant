# ADR-0021 — Proposal: re-evaluate an earlier portfolio variant on the current ledger

- **Status:** Proposed — revision 2, for review, **not implemented**
- **Date:** 2026-09-22
- **Task:** after the first real-data run · **Arch:** §3.2.1, §4.1, §4.2 step 2 · **Open item:** —

## Context

In campaign `c-20260921-171247` the first variant (`778e…`, RSI momentum alone) passed ⑤ and ⑥′. Calibration then added 48 trials; the calibrated RSI failed ④ (PBO 0.549 over 197 configurations), so under "the latest measurement decides, no fallback" (ADR-0013 amendment) RSI left the portfolio, and the rebuilt variant (SMA alone) failed ⑥′. The `778e…` results are stale (INV-52) and nothing can re-run ⑤ → ⑥′ on a variant the rule no longer builds. The user kept the campaign OPEN; revision 1 of this proposal was sent back on four points: member eligibility, the meaning of the added `N`, the order of recording, and retries.

## Decision (proposed)

1. **Scope:** `portfolio-reevaluate HASH` re-runs ⑤ → ⑥′ on a variant already recorded in the OPEN campaign; it changes no member, weight or rule. Freeze is unchanged (only results on the current snapshot count).
2. **Member eligibility — the one exception to "latest measurement decides":** each member trial `t` may be reused only if (a) `t` itself passed ④ (`gate_results.trial_id = t`) and (b) ④ is **re-run now** for `t`'s exact parameters on the current configuration set — the ledger variants of the same `strategy_hash`, which include every later calibration trial — and passes, recorded with `trial_id = t`. The old ④ pass is not current evidence: the configuration set it was computed on has grown, and the confirmation's ④ on the same code failed. This re-check adds a PBO grid, no trial (ADR-0011).
3. **What the added `N` is:** a **policy selection penalty** `P`, one per distinct variant re-evaluated, kept as its own term: ⑤ and ⑥′ use `N_eff + variants + P` (and `N_raw + variants + P`), reported separately. It is not a trial and not in V[SR]. DSR's `N` is the number of independent trials behind a selection (Bailey & López de Prado, 2014); re-running the same code, data and parameters is not an independent trial, so `P` is not derived from the theory — it is a deliberate, conservative price on choosing among variants after seeing results.
4. **Order:** check eligibility (2) → append the penalty (`selection_penalties` row + `VARIANT_REEVALUATION_STARTED` audit event) → take **one** ledger snapshot that includes `P` → run ⑤ then ⑥′ against that snapshot, both results carrying it. A penalty added after the run would make the fresh results stale at once.
5. **Retry vs a new selection — one rule:** re-evaluating variant `V` again is a **technical retry** (no new penalty, same started record) only if its previous re-evaluation recorded no ⑤ result; if a ⑤ result exists (pass or fail), `V` is refused. A **different** variant is a new selection: **at most one variant per campaign may be re-evaluated** — a second, different variant is refused.
6. **Tests to add with the implementation:** penalty recorded before ⑤ and inside its snapshot; both gates report the same snapshot; a retry after a crash before ⑤ adds no penalty; re-running a variant that has a ⑤ result is refused; a second different variant is refused; a member whose fresh ④ fails blocks the re-evaluation; the freeze accepts only results on the snapshot that includes `P`.

## Consequences

- At today's ledger, RSI `778e…` shows DSR 0.999 at N_eff = 4 and 0.986 at N_raw = 54 (read-only, before any `P` and before a fresh ④); whether RSI's parameters pass ④ on the enlarged configuration set is unknown until that re-check runs.
- It re-opens, in a narrow and priced way, the choice the reviewer closed in finding 2; the one-variant cap keeps it from becoming a search over old variants.

## Alternatives considered

- **Keep the current rule (in force now):** the campaign stays OPEN; diversity must come from new, less correlated strategies.
- **Re-evaluate without a penalty, or reuse the old ④ pass:** rejected — a free selection after seeing results, on stale evidence.
