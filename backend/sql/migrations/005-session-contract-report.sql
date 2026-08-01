-- 将合同任务接入用户会话，并持久化可恢复的报告版本。
-- 旧部署执行前请确保 002/003/004 已按顺序完成。

BEGIN;

ALTER TABLE contract_review_tasks
    ADD COLUMN IF NOT EXISTS session_id UUID REFERENCES sessions(session_id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_contract_review_tasks_session_created
    ON contract_review_tasks(session_id, created_at DESC);

CREATE TABLE IF NOT EXISTS contract_review_reports (
    report_id UUID PRIMARY KEY,
    review_id UUID NOT NULL REFERENCES contract_review_tasks(review_id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(session_id) ON DELETE SET NULL,
    report_version INTEGER NOT NULL CHECK (report_version >= 1),
    workflow_status TEXT NOT NULL,
    report JSONB NOT NULL,
    assessment_date DATE NOT NULL DEFAULT CURRENT_DATE,
    input_sha256 VARCHAR(64) NOT NULL DEFAULT '',
    report_sha256 VARCHAR(64) NOT NULL,
    legal_corpus_version TEXT NOT NULL DEFAULT '',
    rule_version TEXT NOT NULL DEFAULT '',
    prompt_version TEXT NOT NULL DEFAULT '',
    model_version TEXT NOT NULL DEFAULT '',
    parser_version TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    UNIQUE (review_id, report_version)
);

CREATE INDEX IF NOT EXISTS idx_contract_review_reports_review_created
    ON contract_review_reports(review_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contract_review_reports_session_created
    ON contract_review_reports(session_id, created_at DESC);

COMMIT;
