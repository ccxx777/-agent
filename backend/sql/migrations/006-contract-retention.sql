-- 合同任务删除与短期留存字段。
BEGIN;

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS retention_policy TEXT NOT NULL DEFAULT 'short';

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP;

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP;

-- 迁移前的存量任务按默认 short 策略回填到期时间，避免 NULL 被当作永久留存。
UPDATE contract_review_tasks
SET expires_at = created_at + INTERVAL '7 days'
WHERE expires_at IS NULL;

DO $$
BEGIN
    ALTER TABLE contract_review_tasks
        ADD CONSTRAINT contract_review_tasks_retention_policy_check
        CHECK (retention_policy IN ('short', 'long_opt_in'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_contract_review_tasks_expiry
    ON contract_review_tasks(expires_at)
    WHERE deleted_at IS NULL;

COMMIT;
