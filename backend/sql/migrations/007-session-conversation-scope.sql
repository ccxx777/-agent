-- 统一会话上下文的迁移状态。
-- 旧部署请在 005/006 之后执行；新数据库由 init/01-tables.sql 自动创建。
BEGIN;

-- NULL 表示旧 session 尚未完成一次性迁移检查；新 session 由应用写入 1。
ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS conversation_scope_version SMALLINT;

ALTER TABLE sessions
    ADD COLUMN IF NOT EXISTS has_contract_context BOOLEAN NOT NULL DEFAULT FALSE;

-- 迁移前无法从已删除/已过期任务还原“是否曾经绑定合同”。为避免旧合同内容
-- 在删除后继续进入模型，所有尚未有 scope 版本的旧 session 统一进入一次性
-- 检查；这会牺牲少量旧普通会话的模型侧连续记忆，但历史 API 仍保留展示能力。
UPDATE sessions AS s
SET has_contract_context = TRUE
WHERE s.conversation_scope_version IS NULL;

COMMIT;
