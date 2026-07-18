"""用户注册与登录业务服务。

本服务负责用户唯一性检查、用户表读写以及认证结果组装。密码哈希和 JWT
编码属于基础设施能力，由 ``app.infrastructure.security`` 提供；HTTP 状态码
转换则留在 API 层。
"""

from __future__ import annotations

import logging
import uuid

from psycopg_pool import AsyncConnectionPool

from app.infrastructure.security import create_access_token, hash_password, verify_password

logger = logging.getLogger(__name__)


class AuthService:
    """通过共享异步连接池实现用户注册和登录用例。"""

    def __init__(self, pool: AsyncConnectionPool):
        self._pool = pool

    async def register(self, username: str, password: str) -> dict:
        """创建用户并签发 Token；用户名重复时抛出 ``ValueError``。"""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT 1 FROM user_profiles WHERE username = %s", (username,)
                )
                if await cur.fetchone():
                    raise ValueError("用户名已存在")

                user_id = str(uuid.uuid4())
                password_hash = hash_password(password)
                await cur.execute(
                    "INSERT INTO user_profiles (user_id, username, password_hash) VALUES (%s, %s, %s)",
                    (user_id, username, password_hash),
                )

        token = create_access_token(user_id, username)
        logger.info("User registered: %s", username)
        return {"user_id": user_id, "username": username, "token": token}

    async def login(self, username: str, password: str) -> dict:
        """校验用户名和密码并签发 Token；认证失败时抛出 ``ValueError``。"""
        async with self._pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute(
                    "SELECT user_id, username, password_hash FROM user_profiles WHERE username = %s",
                    (username,),
                )
                row = await cur.fetchone()

        if not row or not verify_password(password, row[2]):
            raise ValueError("用户名或密码错误")

        return {
            "user_id": str(row[0]),
            "username": row[1],
            "token": create_access_token(str(row[0]), row[1]),
        }
