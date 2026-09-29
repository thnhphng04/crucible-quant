-- Ledger schema v9 (P3-36, arch §3.5, D23, ADR-0038): the backtest engine that measured a trial
-- -- sandbox, cuda or cpu_kernel.
--
-- The column is added by Ledger.open before this script (SQLite has no ADD COLUMN IF NOT EXISTS,
-- and every migration must stay idempotent). ADD COLUMN touches no row, so the append-only
-- triggers stay as they are: a trial recorded before v9 is never backfilled, and its NULL reads
-- as the Docker sandbox, the only engine there was (INV-95 style).
--
-- The precision is deliberately NOT here: it is a campaign constant bound by the lock hash
-- (ADR-0039), like `market_type` in migration 007. The kernel version rides in gate_results.detail.

CREATE INDEX IF NOT EXISTS idx_trials_backtest_engine ON trials(campaign_id, backtest_engine);
