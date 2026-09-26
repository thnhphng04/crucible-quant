-- Ledger schema v7 (P3-11, arch §3.1.11, §4.1, ADR-0033): the (instrument, direction) a
-- candidate was searched for, next to its engine and seed, in both records.
--
-- The four columns are added by Ledger.open before this script (SQLite has no ADD COLUMN IF NOT
-- EXISTS, and every migration must stay idempotent). ADD COLUMN touches no row, so the
-- append-only triggers stay as they are -- which is the point: the 1,325 trials already recorded
-- can never be backfilled, so a NULL scope is resolved as the legacy spot basket at read time
-- and never by an UPDATE (INV-95).
--
-- `market_type` is deliberately NOT here. It is a campaign constant, already bound by the lock
-- hash on `campaigns`, and putting it on every row invites rows that disagree with their own
-- campaign. The UI's legacy_spot / usdt_m_perpetual label comes from the campaign join.

CREATE INDEX IF NOT EXISTS idx_trials_scope
    ON trials(campaign_id, instrument, direction, engine, seed);
CREATE INDEX IF NOT EXISTS idx_gen_scope
    ON generation_log(campaign_id, instrument, direction, engine, seed);
