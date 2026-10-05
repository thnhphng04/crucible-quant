# ADR-0050 — Trial statistics per campaign

- **Status:** Accepted
- **Date:** 2026-10-05
- **Task:** P3-63 · **Arch:** §3.1.6, §3.1.11, §3.2 rows ② ⑤ ⑥′, §4.1, §10 D27 · **Open item:** —

## Context

`N_raw`, `N_eff`, `V[SR]`, the portfolio-variant count and the unmeasured attempts were read from the whole ledger: every trial of every campaign, never reset (§4.1, ADR-0008, ADR-0014). On 2026-10-05 the ledger holds 2,525 trials (`N_eff` 1,490) from eight campaigns, seven of them abandoned and one OPEN. Gate ② then needs about 5.3 years of IS history at target Sharpe 1.5 before any new campaign can take a single trial, so a 1h campaign on about three years of data (MinBTL allows `N ≤ 121`) was blocked outright. The user decided (2026-10-05) that campaigns are independent: each counts only its own trials.

## Decision

- New locks record `derived.trial_scope: campaign_v1` (`validation/trial_scope.py`, written by `new_campaign_derived` and Studio's binding). A lock without the key counts the whole ledger, as before; an unknown tag is refused. Every campaign opened before keeps the ledger-wide count it opened with.
- Under the tag, gate ②'s `N`, gate ⑤'s `N_raw`/`N_eff`/`V[SR]`, the variant count and the unmeasured-attempt sensitivity, gate ⑥′'s stressed DSR, the portfolio's slot ranking, the GP ranking's `RankContext` and the §3.1.11 report count **this campaign's** trials only. The engines and scopes inside one campaign still add up to one `N` (ADR-0033 point unchanged).
- `update_n_eff` clusters the campaign's trials and records the run with its `campaign_id` (migration 010 adds `clustering_runs.campaign_id`). The ledger-wide `trial_stats` view now reads only runs whose `campaign_id` is NULL, so a campaign's run never becomes the ledger's latest.
- `Ledger.snapshot(campaign_id)` counts the campaign's trials and variants and names it (`"scope"`). The freeze compares a ⑤/⑥′ result against a fresh snapshot in the scope it was computed in: another campaign's trials no longer make it stale.
- The trial budget check at opening compares the budget alone with `max_trials_within(IS years)`: a new campaign starts at `N = 0`.
- Unchanged: every trial is still recorded in the one append-only ledger and none is ever removed or reset (P2); the ledger-wide figures are still computed for untagged locks; thresholds are untouched.

## Consequences

- Campaigns on the **same data** no longer charge each other's DSR or MinBTL. Trying again in a new campaign on the same symbols and IS period is not deflated by what earlier campaigns tried — the risk ADR-0033 named ("reset the multiple-testing penalty"). The remaining guard across campaigns is the holdout: each range is claimed and opened once (P6, `holdout_collision`), so a campaign that searched its way to a lucky IS portfolio still meets one untouched test, and no later campaign can reuse that holdout.
- A campaign's own budget is still bounded by MinBTL: about 121 trials for three years of IS at target 1.5, split across its engines, seeds and scopes.
- Schema v10; the review reader accepts v6–v10. The ledger-wide figures on 2026-10-05 are unchanged by the migration.
- Arch: §3.1.6, §3.1.11, §3.2 ⑤, §4.1 and the ledger box say which trials a campaign counts; §10 adds D27. ADR-0006, 0008, 0009, 0014, 0019, 0022 and 0033 carry a status-line amendment.

## Alternatives considered

- Keep the ledger-wide count and wait for longer IS data — blocks the 1h campaign for years.
- Count per data set (symbols × IS period) — closer to the statistics, but the user chose the campaign as the unit; the holdout already bounds what a campaign can reuse.
- Apply the scope to the campaigns opened before too — their gate results were computed ledger-wide; rewriting what they count would change records after the fact.
