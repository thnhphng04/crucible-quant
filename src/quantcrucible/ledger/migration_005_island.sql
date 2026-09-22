-- Ledger schema v5 (P2-03, arch §3.1.5, §3.1.11): the island a candidate evolved on, next to its
-- engine and seed, in both records. Parents and mutation type go in generation_log.detail.
-- The two `island` columns are added by Ledger.open before this script (SQLite has no
-- ADD COLUMN IF NOT EXISTS, and every migration must stay idempotent). ADD COLUMN touches no
-- row, so the append-only triggers stay as they are.

CREATE INDEX IF NOT EXISTS idx_trials_engine_seed ON trials(campaign_id, engine, seed);
CREATE INDEX IF NOT EXISTS idx_gen_engine_seed ON generation_log(campaign_id, engine, seed);
