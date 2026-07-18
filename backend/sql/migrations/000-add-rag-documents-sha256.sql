-- Sentinel 指纹去重需要独立 SHA256 列，不能复用 source 路径。
ALTER TABLE rag_documents ADD COLUMN IF NOT EXISTS sha256 VARCHAR(64);

CREATE UNIQUE INDEX IF NOT EXISTS idx_rag_documents_sha256
    ON rag_documents(sha256)
    WHERE sha256 IS NOT NULL;

