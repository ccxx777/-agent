"""认证 HTTP API。

提供注册、登录和当前用户端点。用户名冲突与认证失败由 ``AuthService`` 用
``ValueError`` 表达，本层负责转换为稳定的 HTTP 409/401 响应。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.schemas.auth import AuthResponse, LoginRequest, RegisterRequest, UserInfo
from app.services.auth_service import AuthService


def create_auth_router(auth_service: AuthService) -> APIRouter:
    """创建绑定指定认证服务的路由。"""
    router = APIRouter(prefix="/api/auth", tags=["Auth"])

    @router.post("/register", response_model=AuthResponse)
    async def register(request: RegisterRequest) -> AuthResponse:
        """注册新用户并返回访问令牌。"""
        try:
            result = await auth_service.register(request.username, request.password)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return AuthResponse(**result)

    @router.post("/login", response_model=AuthResponse)
    async def login(request: LoginRequest) -> AuthResponse:
        """校验凭据并返回新的访问令牌。"""
        try:
            result = await auth_service.login(request.username, request.password)
        except ValueError as error:
            raise HTTPException(status_code=401, detail=str(error)) from error
        return AuthResponse(**result)

    @router.get("/me", response_model=UserInfo)
    async def me(user: dict = Depends(get_current_user)) -> UserInfo:
        """返回 Bearer Token 对应的当前用户。"""
        return UserInfo(user_id=user["user_id"], username=user["username"])

    return router
