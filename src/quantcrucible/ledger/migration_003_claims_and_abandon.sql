-- Ledger schema v3 (ADR-0016 amendment, ADR-0019): the holdout is a consumable across campaigns,
-- and an OPEN campaign can be abandoned without opening it. Idempotent (IF NOT EXISTS).

-- ═══ Holdout claims (§4.2) — taken in one transaction BEFORE any holdout byte is used ═══
-- One claim per campaign (PK), one claim per holdout manifest (UNIQUE), and no claim whose period
-- overlaps an earlier claim's: a used holdout is never a holdout again, whichever campaign asks.
-- A claim is never undone: a crash after it leaves the holdout consumed.
CREATE TABLE IF NOT EXISTS holdout_claims (
    campaign_id       TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
    portfolio_hash    TEXT NOT NULL,
    holdout_range     TEXT NOT NULL,          -- 'YYYY-MM-DD/YYYY-MM-DD', end exclusive
    holdout_lock_hash TEXT NOT NULL UNIQUE,
    claimed_at        TIMESTAMP NOT NULL
);

-- openings made before v3 were claims too
INSERT OR IGNORE INTO holdout_claims
SELECT a.campaign_id, a.portfolio_hash, a.timerange,
       COALESCE(c.holdout_lock_hash, 'pre-v3:' || a.campaign_id), a.accessed_at
FROM holdout_access a JOIN campaigns c ON c.campaign_id = a.campaign_id;

CREATE TRIGGER IF NOT EXISTS holdout_claims_frozen BEFORE INSERT ON holdout_claims
WHEN (SELECT status FROM campaigns WHERE campaign_id = NEW.campaign_id) IS NOT 'FROZEN'
BEGIN SELECT RAISE(ABORT, 'ledger: the holdout is claimed only by a FROZEN campaign'); END;

CREATE TRIGGER IF NOT EXISTS holdout_claims_no_replace BEFORE INSERT ON holdout_claims
WHEN EXISTS (SELECT 1 FROM holdout_claims WHERE campaign_id = NEW.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: holdout_claims key already exists'); END;

CREATE TRIGGER IF NOT EXISTS holdout_claims_no_reuse BEFORE INSERT ON holdout_claims
WHEN EXISTS (
    SELECT 1 FROM holdout_claims c
    WHERE c.campaign_id <> NEW.campaign_id
      AND (c.holdout_lock_hash = NEW.holdout_lock_hash
           OR (substr(NEW.holdout_range, 1, 10) < substr(c.holdout_range, 12, 10)
               AND substr(c.holdout_range, 1, 10) < substr(NEW.holdout_range, 12, 10)))
)
BEGIN SELECT RAISE(ABORT, 'ledger: this holdout (or an overlapping period) was already used'); END;

CREATE TRIGGER IF NOT EXISTS holdout_claims_no_update BEFORE UPDATE ON holdout_claims
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on holdout_claims'); END;
CREATE TRIGGER IF NOT EXISTS holdout_claims_no_delete BEFORE DELETE ON holdout_claims
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on holdout_claims'); END;

CREATE TRIGGER IF NOT EXISTS holdout_access_claimed BEFORE INSERT ON holdout_access
WHEN NOT EXISTS (
    SELECT 1 FROM holdout_claims
    WHERE campaign_id = NEW.campaign_id AND portfolio_hash = NEW.portfolio_hash
)
BEGIN SELECT RAISE(ABORT, 'ledger: a holdout opening must be claimed first'); END;

-- ═══ Abandoned campaigns (O16) — an OPEN campaign closed WITHOUT opening its holdout ═══
-- Its trials stay in N (nothing is reset); its holdout was never claimed, so it stays unused.
CREATE TABLE IF NOT EXISTS campaign_abandonments (
    campaign_id   TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
    abandoned_at  TIMESTAMP NOT NULL,
    reason        TEXT NOT NULL CHECK (length(reason) > 0)
);

CREATE TRIGGER IF NOT EXISTS campaign_abandonments_no_replace BEFORE INSERT ON campaign_abandonments
WHEN EXISTS (SELECT 1 FROM campaign_abandonments WHERE campaign_id = NEW.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: campaign_abandonments key already exists'); END;
CREATE TRIGGER IF NOT EXISTS campaign_abandonments_open BEFORE INSERT ON campaign_abandonments
WHEN (SELECT status FROM campaigns WHERE campaign_id = NEW.campaign_id) IS NOT 'OPEN'
BEGIN SELECT RAISE(ABORT, 'ledger: only an OPEN campaign can be abandoned'); END;
CREATE TRIGGER IF NOT EXISTS campaign_abandonments_no_update BEFORE UPDATE ON campaign_abandonments
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on campaign_abandonments'); END;
CREATE TRIGGER IF NOT EXISTS campaign_abandonments_no_delete BEFORE DELETE ON campaign_abandonments
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on campaign_abandonments'); END;

CREATE TRIGGER IF NOT EXISTS campaigns_abandoned_final BEFORE UPDATE ON campaigns
WHEN EXISTS (SELECT 1 FROM campaign_abandonments WHERE campaign_id = OLD.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger: an abandoned campaign never moves again'); END;
CREATE TRIGGER IF NOT EXISTS trials_not_abandoned BEFORE INSERT ON trials
WHEN EXISTS (SELECT 1 FROM campaign_abandonments WHERE campaign_id = NEW.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger: the campaign is abandoned'); END;
CREATE TRIGGER IF NOT EXISTS gate_results_not_abandoned BEFORE INSERT ON gate_results
WHEN EXISTS (SELECT 1 FROM campaign_abandonments WHERE campaign_id = NEW.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger: the campaign is abandoned'); END;
CREATE TRIGGER IF NOT EXISTS portfolio_variants_not_abandoned BEFORE INSERT ON portfolio_variants
WHEN EXISTS (SELECT 1 FROM campaign_abandonments WHERE campaign_id = NEW.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger: the campaign is abandoned'); END;
