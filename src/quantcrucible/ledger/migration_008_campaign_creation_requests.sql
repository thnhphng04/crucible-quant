-- Ledger schema v8: idempotent explicit campaign creation requests for Studio.
--
-- A Create request key binds to exactly one draft digest, campaign id and dataset id. The row is
-- written in the same transaction as campaigns/campaign_purposes; retrying the same key can return
-- the same campaign, while a reused key with different input is refused by Ledger.

CREATE TABLE IF NOT EXISTS campaign_creation_requests (
    request_key   TEXT PRIMARY KEY,
    draft_digest  TEXT NOT NULL,
    campaign_id   TEXT NOT NULL UNIQUE REFERENCES campaigns(campaign_id),
    dataset_id    TEXT NOT NULL,
    created_at    TIMESTAMP NOT NULL
);

CREATE TRIGGER IF NOT EXISTS campaign_creation_requests_no_replace
BEFORE INSERT ON campaign_creation_requests
WHEN EXISTS (SELECT 1 FROM campaign_creation_requests WHERE request_key = NEW.request_key)
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: campaign_creation_requests key already exists'); END;
CREATE TRIGGER IF NOT EXISTS campaign_creation_requests_no_update
BEFORE UPDATE ON campaign_creation_requests
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: UPDATE on campaign_creation_requests'); END;
CREATE TRIGGER IF NOT EXISTS campaign_creation_requests_no_delete
BEFORE DELETE ON campaign_creation_requests
BEGIN SELECT RAISE(ABORT, 'ledger is append-only: DELETE on campaign_creation_requests'); END;
