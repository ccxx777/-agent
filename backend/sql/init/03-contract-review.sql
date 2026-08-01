-- 合同上传与页级解析任务。
-- 用户合同仅保存在私有文件目录，数据库只保存任务元数据和脱敏页文本。

CREATE TABLE IF NOT EXISTS contract_review_tasks (
    review_id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    session_id UUID REFERENCES sessions(session_id) ON DELETE SET NULL,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/pdf',
    size_bytes BIGINT NOT NULL CHECK (size_bytes > 0),
    sha256 VARCHAR(64) NOT NULL,
    storage_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('queued', 'extracting', 'ready', 'needs_confirmation', 'failed')),
    page_count INTEGER CHECK (page_count IS NULL OR page_count >= 0),
    quality JSONB,
    privacy JSONB,
    extraction_status TEXT NOT NULL DEFAULT 'not_started' CHECK (
        extraction_status IN ('not_started', 'running', 'ready', 'needs_confirmation', 'failed')
    ),
    extraction_result JSONB,
    confirmation_status TEXT NOT NULL DEFAULT 'not_started' CHECK (
        confirmation_status IN ('not_started', 'pending', 'in_progress', 'completed')
    ),
    confirmation_revision INTEGER NOT NULL DEFAULT 0,
    confirmation_result JSONB,
    confirmed_at TIMESTAMP,
    retention_policy TEXT NOT NULL DEFAULT 'short' CHECK (retention_policy IN ('short', 'long_opt_in')),
    expires_at TIMESTAMP,
    deleted_at TIMESTAMP,
    error_message TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contract_review_tasks_user_created
    ON contract_review_tasks(user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_contract_review_tasks_session_created
    ON contract_review_tasks(session_id, created_at DESC);

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

-- 报告是可恢复的版本化产物，不依赖前端内存或一次性 Workflow 响应。
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
