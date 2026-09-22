-- Ledger schema v6 (P2-06, arch §3.1.11): what a campaign is for. A `harness_test` campaign — the
-- phase-2 engine comparison — may build portfolios for the comparison, but is never FROZEN, so it
-- can never claim or open a holdout (holdout_claims requires FROZEN). A campaign without a row
-- here (opened before v6) is a research campaign. Idempotent (IF NOT EXISTS).

CREATE TABLE IF NOT EXISTS campaign_purposes (
    campaign_id   TEXT PRIMARY KEY REFERENCES campaigns(campaign_id),
    purpose       TEXT NOT NULL CHECK (purpose IN ('research', 'harness_test')),
    trial_budget  INTEGER CHECK (trial_budget IS NULL OR trial_budget > 0)
);

CREATE TRIGGER IF NOT EXISTS campaign_purposes_no_replace BEFORE INSERT ON campaign_purposes
WHEN EXISTS (SELECT 1 FROM campaign_purposes WHERE campaign_id = NEW.campaign_id)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: campaign_purposes key already exists'); END;
CREATE TRIGGER IF NOT EXISTS campaign_purposes_no_update BEFORE UPDATE ON campaign_purposes
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on campaign_purposes'); END;
CREATE TRIGGER IF NOT EXISTS campaign_purposes_no_delete BEFORE DELETE ON campaign_purposes
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on campaign_purposes'); END;

CREATE TRIGGER IF NOT EXISTS campaigns_harness_test_never_frozen BEFORE UPDATE OF status ON campaigns
WHEN NEW.status = 'FROZEN' AND EXISTS (
    SELECT 1 FROM campaign_purposes
    WHERE campaign_id = NEW.campaign_id AND purpose = 'harness_test'
)
BEGIN SELECT RAISE(ABORT, 'ledger: a harness-test campaign is never frozen (arch §3.1.11)'); END;
