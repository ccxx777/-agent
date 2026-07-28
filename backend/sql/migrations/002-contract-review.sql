-- 为已经运行的数据库补充合同上传和页级解析表。
-- 新部署由 backend/sql/init/03-contract-review.sql 自动执行；旧部署请手动执行本文件。

BEGIN;

CREATE TABLE IF NOT EXISTS contract_review_tasks (
    review_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/pdf',
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    sha256 VARCHAR(64) NOT NULL,
    storage_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'extracting', 'ready', 'needs_confirmation', 'failed')),
    page_count INTEGER CHECK (page_count IS NULL OR page_count >= 0),
    quality JSONB,
    privacy JSONB,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contract_review_tasks_user_created
    ON contract_review_tasks(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contract_review_tasks_status
    ON contract_review_tasks(status);

CREATE TABLE IF NOT EXISTS contract_review_pages (
    review_id UUID NOT NULL REFERENCES contract_review_tasks(review_id) ON DELETE CASCADE,
    page_no INTEGER NOT NULL CHECK (page_no >= 1),
    mode TEXT NOT NULL CHECK (mode IN ('native', 'hybrid', 'scanned')),
    redacted_text TEXT NOT NULL DEFAULT '',
    ocr_used BOOLEAN NOT NULL DEFAULT FALSE,
    quality_flags TEXT[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (review_id, page_no)
);

COMMIT;
