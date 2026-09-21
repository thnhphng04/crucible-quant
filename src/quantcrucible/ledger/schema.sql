-- Crucible Quant — trial ledger. Architecture §4.1 + §4.2, with the additions of ADR-0002:
--   campaigns.lock_hash / holdout_lock_hash, trials.candidate_id, clustering_runs + trial_clusters
--   (instead of trials.cluster_id), gate_results.
-- APPEND-ONLY: every table rejects DELETE; every table except `campaigns` rejects UPDATE;
-- `campaigns` accepts only the status moves OPEN → FROZEN → BURNED.

-- ═══ Research campaigns (§4.2) ═══
CREATE TABLE campaigns (
    campaign_id       TEXT PRIMARY KEY,
    started_at        TIMESTAMP NOT NULL,
    holdout_range     TEXT NOT NULL,
    lock_hash         TEXT NOT NULL,            -- SHA256 of evaluation.lock.yaml (§10.1)
    holdout_lock_hash TEXT,                     -- SHA256 of holdout.lock (§4.2 layer 2)
    status            TEXT NOT NULL CHECK (status IN ('OPEN','FROZEN','BURNED'))
);

-- ═══ RECORD 1: AUDIT LOG — all activity, NOT used for statistics (§4.1) ═══
CREATE TABLE generation_log (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    run_id         TEXT NOT NULL,
    campaign_id    TEXT NOT NULL REFERENCES campaigns(campaign_id),
    engine         TEXT NOT NULL,
    seed           INTEGER NOT NULL,
    evolve_scope   TEXT,
    agent          TEXT NOT NULL,
    model_used     TEXT NOT NULL,
    cell_id        TEXT,
    event          TEXT NOT NULL,
    strategy_hash  TEXT,
    drift_delta    REAL,
    detail         JSON
);

-- ═══ RECORD 2: STATISTICAL TRIALS — only configurations that reached ③ Backtest IS (§4.1) ═══
CREATE TABLE trials (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    run_id         TEXT NOT NULL,
    campaign_id    TEXT NOT NULL REFERENCES campaigns(campaign_id),
    candidate_id   TEXT NOT NULL,               -- links gate_results (ADR-0002)
    engine         TEXT NOT NULL,
    seed           INTEGER NOT NULL,
    evolve_scope   TEXT,
    strategy_hash  TEXT NOT NULL,
    hypothesis     TEXT,
    params         JSON NOT NULL,
    universe       TEXT NOT NULL,
    timeframe      TEXT NOT NULL,
    timerange      TEXT NOT NULL,
    cell_id        TEXT,
    source         TEXT NOT NULL CHECK (source IN ('evolution','param_opt','manual')),
    sharpe_is      REAL NOT NULL,
    returns_path   TEXT NOT NULL,
    gate_failed    TEXT,
    verdict        TEXT NOT NULL
);

-- ═══ N_eff clustering (ADR-0002 — replaces trials.cluster_id; history kept) ═══
CREATE TABLE clustering_runs (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    method         TEXT NOT NULL,
    n_trials       INTEGER NOT NULL
);

CREATE TABLE trial_clusters (
    clustering_run INTEGER NOT NULL REFERENCES clustering_runs(id),
    trial_id       INTEGER NOT NULL REFERENCES trials(id),
    cluster_id     INTEGER NOT NULL,
    PRIMARY KEY (clustering_run, trial_id)
);

-- ═══ Every GateResult, pass or fail (§3.2; ADR-0002) ═══
CREATE TABLE gate_results (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    ts             TIMESTAMP NOT NULL,
    campaign_id    TEXT NOT NULL REFERENCES campaigns(campaign_id),
    candidate_id   TEXT NOT NULL,
    strategy_hash  TEXT,
    trial_id       INTEGER REFERENCES trials(id),   -- NULL before gate ③
    gate           TEXT NOT NULL,
    passed         INTEGER NOT NULL CHECK (passed IN (0, 1)),
    value          REAL,
    reason         TEXT NOT NULL,
    detail         JSON
);

-- ═══ Portfolio variants (§3.2.1) — each row is also a selection ═══
CREATE TABLE portfolio_variants (
    portfolio_hash TEXT PRIMARY KEY,
    campaign_id    TEXT NOT NULL REFERENCES campaigns(campaign_id),
    rule_config    JSON NOT NULL,
    members        JSON NOT NULL,
    sharpe_is      REAL,
    returns_path   TEXT,
    ts             TIMESTAMP NOT NULL
);

-- ═══ Holdout registry (§4.2) — PRIMARY KEY(campaign_id): one opening per campaign ═══
CREATE TABLE holdout_access (
    campaign_id     TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
    portfolio_hash  TEXT NOT NULL REFERENCES portfolio_variants(portfolio_hash),
    frozen_at       TIMESTAMP NOT NULL,
    accessed_at     TIMESTAMP NOT NULL,
    timerange       TEXT NOT NULL,
    verdict         TEXT NOT NULL CHECK (verdict IN ('PASS','FAIL')),
    sharpe_oos      REAL,
    CHECK (frozen_at < accessed_at)
);

CREATE INDEX idx_hash ON trials(strategy_hash);
CREATE INDEX idx_cell ON trials(cell_id);
CREATE INDEX idx_gen_cell ON generation_log(cell_id);
CREATE INDEX idx_gate_candidate ON gate_results(candidate_id);

-- ═══ Views ═══

-- DSR inputs: every statistical trial, every verdict, every run, NEVER reset.
-- n_eff = clusters in the latest clustering run + trials that run did not cover (each counts as
-- its own cluster — conservative). No clustering yet ⇒ n_eff = n_raw.
CREATE VIEW trial_stats AS
WITH latest AS (SELECT MAX(id) AS run FROM clustering_runs),
     covered AS (
         SELECT tc.trial_id, tc.cluster_id
         FROM trial_clusters tc JOIN latest ON tc.clustering_run = latest.run
     )
SELECT (SELECT COUNT(*) FROM trials) AS n_raw,
       (SELECT COUNT(DISTINCT cluster_id) FROM covered)
         + (SELECT COUNT(*) FROM trials WHERE id NOT IN (SELECT trial_id FROM covered)) AS n_eff,
       (SELECT AVG(sharpe_is * sharpe_is) - AVG(sharpe_is) * AVG(sharpe_is) FROM trials) AS var_sr;

CREATE VIEW total_portfolio_variants AS SELECT COUNT(*) AS n FROM portfolio_variants;

-- Fill-bias alarm: code-generation failure rate > 50%, read from the AUDIT LOG.
CREATE VIEW starved_cells AS
SELECT cell_id,
       SUM(event IN ('COMPILE_FAIL','AST_REJECT')) * 1.0
         / NULLIF(SUM(event = 'LLM_CALL' AND agent = 'coding'), 0) AS fail_rate,
       SUM(event = 'LLM_CALL' AND agent = 'coding') AS attempts
FROM generation_log
WHERE cell_id IS NOT NULL
GROUP BY cell_id
HAVING fail_rate > 0.5;

-- ═══ Append-only enforcement ═══

CREATE TRIGGER campaigns_insert_open BEFORE INSERT ON campaigns
WHEN NEW.status <> 'OPEN'
BEGIN SELECT RAISE(ABORT, 'ledger: a campaign must start OPEN'); END;

CREATE TRIGGER campaigns_update BEFORE UPDATE ON campaigns
WHEN NOT (
        NEW.campaign_id = OLD.campaign_id
    AND NEW.started_at = OLD.started_at
    AND NEW.holdout_range = OLD.holdout_range
    AND NEW.lock_hash = OLD.lock_hash
    AND NEW.holdout_lock_hash IS OLD.holdout_lock_hash
    AND ((OLD.status = 'OPEN' AND NEW.status = 'FROZEN')
      OR (OLD.status = 'FROZEN' AND NEW.status = 'BURNED'))
)
BEGIN SELECT RAISE(ABORT, 'ledger: campaigns only move OPEN -> FROZEN -> BURNED'); END;

CREATE TRIGGER holdout_access_frozen BEFORE INSERT ON holdout_access
WHEN (SELECT status FROM campaigns WHERE campaign_id = NEW.campaign_id) IS NOT 'FROZEN'
BEGIN SELECT RAISE(ABORT, 'ledger: the holdout opens only for a FROZEN campaign'); END;

CREATE TRIGGER campaigns_no_delete BEFORE DELETE ON campaigns
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on campaigns'); END;
CREATE TRIGGER generation_log_no_delete BEFORE DELETE ON generation_log
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on generation_log'); END;
CREATE TRIGGER generation_log_no_update BEFORE UPDATE ON generation_log
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on generation_log'); END;
CREATE TRIGGER trials_no_delete BEFORE DELETE ON trials
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on trials'); END;
CREATE TRIGGER trials_no_update BEFORE UPDATE ON trials
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on trials'); END;
CREATE TRIGGER clustering_runs_no_delete BEFORE DELETE ON clustering_runs
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on clustering_runs'); END;
CREATE TRIGGER clustering_runs_no_update BEFORE UPDATE ON clustering_runs
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on clustering_runs'); END;
CREATE TRIGGER trial_clusters_no_delete BEFORE DELETE ON trial_clusters
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on trial_clusters'); END;
CREATE TRIGGER trial_clusters_no_update BEFORE UPDATE ON trial_clusters
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on trial_clusters'); END;
CREATE TRIGGER gate_results_no_delete BEFORE DELETE ON gate_results
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on gate_results'); END;
CREATE TRIGGER gate_results_no_update BEFORE UPDATE ON gate_results
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on gate_results'); END;
CREATE TRIGGER portfolio_variants_no_delete BEFORE DELETE ON portfolio_variants
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on portfolio_variants'); END;
CREATE TRIGGER portfolio_variants_no_update BEFORE UPDATE ON portfolio_variants
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on portfolio_variants'); END;
CREATE TRIGGER holdout_access_no_delete BEFORE DELETE ON holdout_access
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on holdout_access'); END;
CREATE TRIGGER holdout_access_no_update BEFORE UPDATE ON holdout_access
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on holdout_access'); END;
