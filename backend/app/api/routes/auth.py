"""鉴权相关路由：登录 / 登出 / 当前主体。

登录：常量时间比较 WEBUI_AUTH_SECRET → 签发无状态 token（HMAC）→ 写 HttpOnly cookie 并回 token。
登出：清 cookie（无状态侧无法吊销单 token，轮换密钥方可全量吊销）。
"""

from __future__ import annotations

import hmac
import time

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel

from ...core import config as config_module
from ...modules.auth import SESSION_COOKIE, AuthPrincipal, issue, require_auth

router = APIRouter(prefix="/auth")


class LoginRequest(BaseModel):
    secret: str


class LoginResponse(BaseModel):
    token: str
    expires_in: int
    subject: str


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest, response: Response) -> LoginResponse:
    """用 WEBUI_AUTH_SECRET 登录，换会话 token。

    常量时间比较 secret；失败返回 401（非 500）。成功写 HttpOnly + SameSite=Strict cookie。
    """

    settings = config_module.settings
    if not settings.auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="鉴权未配置（WEBUI_AUTH_SECRET 为空）。请在环境变量注入强随机密钥。",
        )
    # 常量时间比较，防时序侧信道：两侧都先编码为 utf-8。
    supplied = req.secret.encode("utf-8")
    expected = settings.auth_secret.encode("utf-8")
    if not hmac.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="密钥错误")
    # 服务重启后旧 token 仍有效（无状态），故签名密钥不变即可继续验。
    token = issue(
        subject="admin",
        secret=settings.auth_secret,
        ttl_seconds=settings.session_ttl_seconds,
        now=time.time(),
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        samesite="strict",
        secure=False,  # 走 SSH 隧道 http://localhost，非 HTTPS；同源 + HttpOnly 已足够
        path="/",
    )
    return LoginResponse(token=token, expires_in=settings.session_ttl_seconds, subject="admin")


@router.post("/logout")
async def logout(response: Response) -> dict:
    """登出：清 cookie。无状态 token 无法服务端单条吊销，轮换密钥方可全量吊销。"""
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
async def whoami(principal: AuthPrincipal = Depends(require_auth)) -> dict:
    """返回当前已登录主体（前端校验登录态用）。"""
    return {"subject": principal.subject}
