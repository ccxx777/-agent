"""FastAPI 请求级依赖。

当前只包含 Bearer Token 用户解析。它不访问数据库，也不创建 Service；应用级
对象的装配统一在 ``main.lifespan`` 中完成。
"""

from __future__ import annotations

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.infrastructure.security import decode_token

security = HTTPBearer()


def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> dict:
    """从 Bearer Token 解析当前用户。

    所有需要登录的端点复用此依赖：
        @router.get("/xxx")
        async def xxx(user: dict = Depends(get_current_user)):
            ...

    返回 ``{"user_id": "...", "username": "..."}``；Token 无效或过期时
    抛出 HTTP 401。
    """
    if credentials is None:
        raise HTTPException(status_code=401, detail="请先登录")
    user = decode_token(credentials.credentials)
    if user is None:
        raise HTTPException(status_code=401, detail="登录已过期，请重新登录")
    return {"user_id": user["sub"], "username": user["username"]}
