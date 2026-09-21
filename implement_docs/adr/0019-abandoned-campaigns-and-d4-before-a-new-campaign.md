# ADR-0019 — Abandoned campaigns, and D4 before a new campaign

- **Status:** Accepted
- **Date:** 2026-09-21
- **Task:** P1-12 review · **Arch:** §4.2 (procedure step 5, added), §10 D4, §10.1 · **Open item:** closes O16

## Context

The real-data campaign opened in phase 0 predates `derived.sizing`, so gate ③ refuses its lock. It is OPEN, and §4.2 only knew OPEN → FROZEN → BURNED: the only way out was to open the holdout, for nothing. The user chose an `ABANDONED` state with an audit trail, keeping the old lock. The same review noted that `holdout_pass` (D4) is locked with the campaign, so it must be decided before a new campaign opens, not at the holdout.

## Decision

1. **`ABANDONED`** = a row in the append-only `campaign_abandonments` table (schema v3) with a non-empty reason, plus a `CAMPAIGN_ABANDONED` audit event (`validation/freeze.py::abandon`, CLI `campaign-abandon --reason`). `Ledger.campaign()` reports the status as `ABANDONED`. The `campaigns.status` CHECK is left as it is: changing it would mean rebuilding a table other tables reference.
2. **Only OPEN, once, final:** triggers refuse abandoning a non-OPEN campaign, and after abandonment refuse any status move and any new `trials`, `gate_results` or `portfolio_variants` row for it.
3. **Nothing is reset:** the campaign's trials stay in `N` and `V[SR]` (the ledger-wide `trial_stats`). Its holdout was never claimed, so it is still unused and may serve the next campaign.
4. **The old lock stays as it is:** it is not edited. When the next campaign opens, `config.lock.open_campaign` moves it byte for byte to `config/locks/<campaign_id>.lock.yaml`; its SHA256 still matches the ledger.
5. **D4 first:** `current_campaign` opens a new campaign — no lock yet, or its campaign BURNED or ABANDONED — only if `research.holdout_pass` is set (`CampaignNotOpened` otherwise). Campaign ids get a `-N` suffix when two open within one second.

## Consequences

- The real campaign can be closed with `uv run python -m quantcrucible.cli campaign-abandon --reason "..."` — an irreversible ledger write, left to the user to run.
- The next real campaign still waits on D4: its meaning (ADR-0016 reads it as a minimum annualized OOS Sharpe) and its value. The 0.5 used in tests is not a basis for choosing it.
- Tests: `tests/ledger/test_db.py::test_abandoned_campaign_is_final_and_keeps_its_trials`, `::test_only_an_open_campaign_can_be_abandoned`; `tests/validation/test_run.py::test_abandoned_campaign_is_replaced`, `::test_a_new_campaign_needs_d4`; `tests/config/test_lock.py::test_new_campaign_after_an_abandoned_one`.

## Alternatives considered

- **Re-carve a fresh holdout:** rejected here — it throws away an unused holdout and the free history is short (§4.2 "consumable").
- **Rebuild `campaigns` with a four-state CHECK:** rejected — needs foreign keys off during the migration of an append-only ledger; a separate table gives the same guarantees with triggers.
