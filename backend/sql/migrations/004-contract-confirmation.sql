-- 合同事实确认层：用户动作、有效快照和追加式审计。
-- 执行顺序：002-contract-review.sql -> 003-contract-extraction.sql -> 本文件。
BEGIN;

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS confirmation_status TEXT NOT NULL DEFAULT 'not_started';

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS confirmation_revision INTEGER NOT NULL DEFAULT 0;

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS confirmation_result JSONB;

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS confirmed_at TIMESTAMP;

DO $$
BEGIN
    ALTER TABLE contract_review_tasks
        ADD CONSTRAINT contract_review_tasks_confirmation_status_check
        CHECK (confirmation_status IN ('not_started', 'pending', 'in_progress', 'completed'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS contract_review_fact_confirmations (
    confirmation_id UUID PRIMARY KEY,
    review_id UUID NOT NULL REFERENCES contract_review_tasks(review_id) ON DELETE CASCADE,
    fact_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('confirm', 'correct', 'supplement', 'not_applicable', 'defer')),
    user_value JSONB,
    note TEXT,
    base_revision INTEGER NOT NULL CHECK (base_revision >= 0),
    request_id TEXT,
    created_by UUID NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contract_confirmation_review_created
    ON contract_review_fact_confirmations(review_id, created_at);

CREATE INDEX IF NOT EXISTS idx_contract_confirmation_review_fact
    ON contract_review_fact_confirmations(review_id, fact_id, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS uq_contract_confirmation_request_fact
    ON contract_review_fact_confirmations(review_id, request_id, fact_id)
    WHERE request_id IS NOT NULL;

COMMIT;
