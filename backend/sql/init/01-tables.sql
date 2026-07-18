-- AI Assistant 数据库初始化 DDL
-- 首次启动时由 docker-entrypoint-initdb.d 自动执行

-- 用户画像
CREATE TABLE IF NOT EXISTS user_profiles (
    user_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR UNIQUE NOT NULL,
    password_hash VARCHAR NOT NULL,
    name TEXT,
    age INTEGER,
    gender TEXT CHECK (gender IN ('M', 'F', 'O')),
    hometown TEXT,
    tags JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 为已有部署补加 username / password_hash 列（幂等）
DO $$ BEGIN
    ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS username VARCHAR;
    ALTER TABLE user_profiles ADD COLUMN IF NOT EXISTS password_hash VARCHAR;
EXCEPTION WHEN duplicate_column THEN NULL;
END $$;
CREATE UNIQUE INDEX IF NOT EXISTS idx_user_profiles_username ON user_profiles(username)
    WHERE username IS NOT NULL;

-- 会话记录
CREATE TABLE IF NOT EXISTS sessions (
    session_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    title TEXT,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- RAG 文档管理
CREATE TABLE IF NOT EXISTS rag_documents (
    doc_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    title TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    source TEXT,
    tags TEXT[],
    user_id UUID,
    chunk_count INTEGER DEFAULT 0,
    indexed_at TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
