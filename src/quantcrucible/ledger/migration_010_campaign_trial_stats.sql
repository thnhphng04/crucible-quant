-- Ledger schema v10 (P3-63, arch §4.1, D27, ADR-0050): a clustering run may belong to one
-- campaign.
--
-- A campaign locked with `derived.trial_scope: campaign_v1` counts only its own trials, so its
-- N_eff comes from a clustering of its own trials, recorded with its campaign_id. The column is
-- added by Ledger.open before this script (SQLite has no ADD COLUMN IF NOT EXISTS, and every
-- migration must stay idempotent). ADD COLUMN touches no row: every run recorded before v10 is
-- ledger-wide, and reads as NULL.
--
-- The ledger-wide view must then read only ledger-wide runs. Without the filter, a campaign's
-- clustering would become "the latest run" of the whole ledger and shrink every untagged
-- campaign's N_eff. The view is otherwise the one in schema.sql, unchanged.

DROP VIEW IF EXISTS trial_stats;
CREATE VIEW trial_stats AS
WITH latest AS (SELECT MAX(id) AS run FROM clustering_runs WHERE campaign_id IS NULL),
     covered AS (
         SELECT tc.trial_id, tc.cluster_id
         FROM trial_clusters tc JOIN latest ON tc.clustering_run = latest.run
     )
SELECT
    (SELECT COUNT(*) FROM trials) AS n_raw,
    (SELECT COUNT(DISTINCT cluster_id) FROM covered)
      + (SELECT COUNT(*) FROM trials WHERE id NOT IN (SELECT trial_id FROM covered)) AS n_eff,
    (SELECT AVG(sharpe_is * sharpe_is) - AVG(sharpe_is) * AVG(sharpe_is) FROM trials) AS var_sr;

CREATE INDEX IF NOT EXISTS idx_clustering_campaign ON clustering_runs(campaign_id, id);
CREATE INDEX IF NOT EXISTS idx_gate_campaign ON gate_results(campaign_id, gate);
