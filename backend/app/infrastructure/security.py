"""密码哈希和 JWT 令牌基础设施。

本模块不查询用户表，只提供无状态的密码与 Token 原语。Argon2id 参数沿用
原项目的 2 核服务器配置，结构迁移不会改变已有密码哈希的验证方式。
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from argon2.low_level import Type
from jose import JWTError, jwt

ALGORITHM = "HS256"
TOKEN_EXPIRE_HOURS = 24
_MIN_SECRET_KEY_LENGTH = 32


def _secret_key() -> str:
    """读取生产签名密钥；缺失或过短时拒绝继续运行。"""

    value = os.getenv("AUTH_SECRET_KEY", "").strip()
    if len(value) < _MIN_SECRET_KEY_LENGTH:
        raise RuntimeError(
            "AUTH_SECRET_KEY must be configured with at least 32 characters; "
            "the development fallback is disabled"
        )
    return value


def validate_security_config() -> None:
    """在应用启动时验证 JWT 配置，避免服务以可伪造密钥运行。"""

    _secret_key()

_password_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=65536,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,
)


def hash_password(password: str) -> str:
    """使用 Argon2id 生成不可逆密码哈希。"""
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """验证明文密码；格式错误或不匹配时统一返回 ``False``。"""
    try:
        return _password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


def create_access_token(user_id: str, username: str) -> str:
    """签发包含用户标识、用户名和 24 小时过期时间的 JWT。"""
    expires_at = datetime.now(timezone.utc) + timedelta(hours=TOKEN_EXPIRE_HOURS)
    return jwt.encode(
        {"sub": user_id, "username": username, "exp": expires_at},
        _secret_key(),
        algorithm=ALGORITHM,
    )


def decode_token(token: str) -> dict | None:
    """验证并解码 JWT；无效或过期时返回 ``None``。"""
    try:
        return jwt.decode(token, _secret_key(), algorithms=[ALGORITHM])
    except (JWTError, RuntimeError):
        return None
