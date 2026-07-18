-- 长期记忆架构: 滑动窗口 + 动态摘要
--
-- 在初次 init（01-tables.sql）之上补齐:
--   sessions.summary  — 压缩的对话摘要
--   chat_messages     — 对话历史归档 (与 LangGraph checkpoints 解耦)

-- ── sessions: 新增 summary 列 ──
ALTER TABLE sessions ADD COLUMN IF NOT EXISTS summary TEXT DEFAULT '';

-- ── chat_messages: 对话历史归档 ──
CREATE TABLE IF NOT EXISTS chat_messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id UUID NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'ai')),
    content TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id);
CREATE INDEX IF NOT EXISTS idx_chat_messages_created ON chat_messages(session_id, created_at);
