-- Ledger schema v2 (ADR-0002 amendment): refuse any INSERT whose key already exists.
-- `INSERT OR REPLACE` / `REPLACE` delete the old row first, and that implicit delete fires no
-- DELETE trigger unless `recursive_triggers` is on — which a raw connection controls, not us.
-- A BEFORE INSERT trigger runs before conflict resolution, so it also stops upserts.

CREATE TRIGGER campaigns_no_replace BEFORE INSERT ON campaigns
WHEN EXISTS (SELECT 1 FROM campaigns WHERE campaign_id = NEW.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: campaigns key already exists'); END;
CREATE TRIGGER generation_log_no_replace BEFORE INSERT ON generation_log
WHEN EXISTS (SELECT 1 FROM generation_log WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: generation_log key already exists'); END;
CREATE TRIGGER trials_no_replace BEFORE INSERT ON trials
WHEN EXISTS (SELECT 1 FROM trials WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: trials key already exists'); END;
CREATE TRIGGER clustering_runs_no_replace BEFORE INSERT ON clustering_runs
WHEN EXISTS (SELECT 1 FROM clustering_runs WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: clustering_runs key already exists'); END;
CREATE TRIGGER trial_clusters_no_replace BEFORE INSERT ON trial_clusters
WHEN EXISTS (SELECT 1 FROM trial_clusters WHERE clustering_run = NEW.clustering_run AND trial_id = NEW.trial_id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: trial_clusters key already exists'); END;
CREATE TRIGGER gate_results_no_replace BEFORE INSERT ON gate_results
WHEN EXISTS (SELECT 1 FROM gate_results WHERE id = NEW.id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: gate_results key already exists'); END;
CREATE TRIGGER portfolio_variants_no_replace BEFORE INSERT ON portfolio_variants
WHEN EXISTS (SELECT 1 FROM portfolio_variants WHERE portfolio_hash = NEW.portfolio_hash)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: portfolio_variants key already exists'); END;
CREATE TRIGGER holdout_access_no_replace BEFORE INSERT ON holdout_access
WHEN EXISTS (SELECT 1 FROM holdout_access WHERE campaign_id = NEW.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: holdout_access key already exists'); END;
