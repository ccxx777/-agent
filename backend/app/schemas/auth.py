"""认证接口请求与响应模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


class RegisterRequest(BaseModel):
    """注册请求；要求用户名、密码长度合规且两次密码一致。"""

    username: str = Field(..., min_length=3, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    confirm_password: str = Field(..., min_length=8, max_length=128)

    @model_validator(mode="after")
    def passwords_match(self) -> "RegisterRequest":
        """在进入 Service 前拒绝两次密码不一致的请求。"""
        if self.password != self.confirm_password:
            raise ValueError("两次输入的密码不一致")
        return self


class LoginRequest(BaseModel):
    """登录请求；具体凭据校验由 AuthService 完成。"""

    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class AuthResponse(BaseModel):
    """注册和登录共用的成功响应。"""

    user_id: str
    username: str
    token: str


class UserInfo(BaseModel):
    """不暴露 Token 和密码信息的当前用户视图。"""

    user_id: str
    username: str
