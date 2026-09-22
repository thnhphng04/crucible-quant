-- Ledger schema v4 (ADR-0017 amendment 2): calibration (§3.2.1 5b) runs once per strategy per
-- campaign, with the locked budget. A run is registered BEFORE its first attempt and closed when
-- its search ends; a second run is refused (the one exception, a technical retry of a run that
-- measured nothing, reuses the same row). Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS calibration_runs (
    campaign_id    TEXT NOT NULL REFERENCES campaigns(campaign_id),
    candidate_id   TEXT NOT NULL,
    strategy_hash  TEXT NOT NULL,
    budget         INTEGER NOT NULL CHECK (budget >= 1),
    started_at     TIMESTAMP NOT NULL,
    PRIMARY KEY (campaign_id, candidate_id),
    UNIQUE (campaign_id, strategy_hash)
);

CREATE TABLE IF NOT EXISTS calibration_finishes (
    campaign_id    TEXT NOT NULL,
    candidate_id   TEXT NOT NULL,
    finished_at    TIMESTAMP NOT NULL,
    outcome        TEXT NOT NULL CHECK (outcome IN ('finished', 'stopped')),
    attempts       INTEGER NOT NULL,
    trials         INTEGER NOT NULL,
    errors         INTEGER NOT NULL,
    PRIMARY KEY (campaign_id, candidate_id),
    FOREIGN KEY (campaign_id, candidate_id) REFERENCES calibration_runs(campaign_id, candidate_id)
);

-- runs recorded before v4 are known from their audit event; the budget they used is the number
-- of attempts they made
INSERT OR IGNORE INTO calibration_runs
SELECT campaign_id, json_extract(detail, '$.candidate_id'), strategy_hash,
       json_extract(detail, '$.attempts'), ts
FROM generation_log WHERE event = 'CALIBRATION_FINISHED' ORDER BY id;
INSERT OR IGNORE INTO calibration_finishes
SELECT campaign_id, json_extract(detail, '$.candidate_id'), ts, 'finished',
       json_extract(detail, '$.attempts'), json_extract(detail, '$.trials'),
       COALESCE(json_extract(detail, '$.errors'), 0)
FROM generation_log WHERE event = 'CALIBRATION_FINISHED' ORDER BY id;

-- runs older than that event are known from their rows: trials of run 'calib-<candidate id>',
-- one gate-③ result per attempt '<candidate id>-optNNN' (measured or not)
CREATE TEMP TABLE v4_old_runs AS
SELECT t.campaign_id AS campaign_id, substr(t.run_id, 7) AS candidate_id,
       MIN(t.strategy_hash) AS strategy_hash, MIN(t.ts) AS started_at, MAX(t.ts) AS finished_at
FROM trials t WHERE t.source = 'param_opt' AND substr(t.run_id, 1, 6) = 'calib-'
GROUP BY t.campaign_id, t.run_id;
CREATE TEMP TABLE v4_old_counts AS
SELECT r.campaign_id, r.candidate_id, r.strategy_hash, r.started_at, r.finished_at,
       (SELECT COUNT(*) FROM gate_results g WHERE g.campaign_id = r.campaign_id
          AND g.gate = 'g3_is'
          AND substr(g.candidate_id, 1, length(r.candidate_id) + 4) = r.candidate_id || '-opt')
         AS attempts,
       (SELECT COUNT(*) FROM trials t WHERE t.campaign_id = r.campaign_id
          AND substr(t.candidate_id, 1, length(r.candidate_id) + 4) = r.candidate_id || '-opt')
         AS trials
FROM v4_old_runs r;
INSERT OR IGNORE INTO calibration_runs
SELECT campaign_id, candidate_id, strategy_hash, MAX(attempts, 1), started_at
FROM v4_old_counts ORDER BY started_at;
INSERT OR IGNORE INTO calibration_finishes
SELECT c.campaign_id, c.candidate_id, c.finished_at, 'finished', c.attempts, c.trials,
       c.attempts - c.trials
FROM v4_old_counts c JOIN calibration_runs r
  ON r.campaign_id = c.campaign_id AND r.candidate_id = c.candidate_id;
DROP TABLE v4_old_counts;
DROP TABLE v4_old_runs;

CREATE TRIGGER IF NOT EXISTS calibration_runs_open BEFORE INSERT ON calibration_runs
WHEN (SELECT status FROM campaigns WHERE campaign_id = NEW.campaign_id) IS NOT 'OPEN'
  OR EXISTS (SELECT 1 FROM campaign_abandonments WHERE campaign_id = NEW.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger: calibration runs only in an OPEN campaign'); END;
CREATE TRIGGER IF NOT EXISTS calibration_runs_no_replace BEFORE INSERT ON calibration_runs
WHEN EXISTS (SELECT 1 FROM calibration_runs WHERE campaign_id = NEW.campaign_id
             AND (candidate_id = NEW.candidate_id OR strategy_hash = NEW.strategy_hash))
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: this strategy was already calibrated in this campaign'); END;
CREATE TRIGGER IF NOT EXISTS calibration_runs_no_update BEFORE UPDATE ON calibration_runs
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on calibration_runs'); END;
CREATE TRIGGER IF NOT EXISTS calibration_runs_no_delete BEFORE DELETE ON calibration_runs
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on calibration_runs'); END;

CREATE TRIGGER IF NOT EXISTS calibration_finishes_no_replace BEFORE INSERT ON calibration_finishes
WHEN EXISTS (SELECT 1 FROM calibration_finishes WHERE campaign_id = NEW.campaign_id
             AND candidate_id = NEW.candidate_id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: calibration_finishes key already exists'); END;
CREATE TRIGGER IF NOT EXISTS calibration_finishes_within_budget BEFORE INSERT ON calibration_finishes
WHEN NEW.attempts > (SELECT budget FROM calibration_runs WHERE campaign_id = NEW.campaign_id
                     AND candidate_id = NEW.candidate_id)
BEGIN SELECT RAISE(ABORT, 'ledger: more attempts than the registered budget'); END;
CREATE TRIGGER IF NOT EXISTS calibration_finishes_no_update BEFORE UPDATE ON calibration_finishes
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on calibration_finishes'); END;
CREATE TRIGGER IF NOT EXISTS calibration_finishes_no_delete BEFORE DELETE ON calibration_finishes
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on calibration_finishes'); END;
