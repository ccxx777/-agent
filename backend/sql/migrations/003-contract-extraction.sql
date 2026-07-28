-- 为已有部署补充合同条款与事实提取结果字段。
-- 文件解析状态与事实提取状态独立，避免模型失败导致原始合同重新解析。

BEGIN;

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS extraction_status TEXT NOT NULL DEFAULT 'not_started';

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS extraction_result JSONB;

DO $$
BEGIN
    ALTER TABLE contract_review_tasks
        ADD CONSTRAINT contract_review_tasks_extraction_status_check
        CHECK (extraction_status IN ('not_started', 'running', 'ready', 'needs_confirmation', 'failed'));
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE INDEX IF NOT EXISTS idx_contract_review_tasks_extraction_status
    ON contract_review_tasks(extraction_status);

COMMIT;
