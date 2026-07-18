-- 手动迁移：移除已退出生产路径的旧业务表。
-- 执行前必须先备份数据库；本文件不会被 docker-entrypoint 自动执行。

BEGIN;

DROP TABLE IF EXISTS token_usage;
DROP TABLE IF EXISTS messages;

COMMIT;
