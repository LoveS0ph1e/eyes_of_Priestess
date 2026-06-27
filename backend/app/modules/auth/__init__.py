"""鉴权模块（P0，硬约束）—— 真实实现。

EverOS 应用层裸奔（CORS=* + 无鉴权），WebUI 一旦能写记忆即绕过所有隔离铁律。
故本服务即便走 SSH 隧道也必须有自身登录鉴权——这不是可选项。

安全边界（已实现）：
  - 未登录态访问任何写接口 → 401。
  - settings.auth_secret 为空（未配置）→ 写接口 503，拒绝裸奔。
  - 鉴权密钥从环境变量注入，绝不硬编码、绝不进 git。
  - token 校验：HMAC 签名 + 常量时间比较 + 过期失效（见 token.py）。
  - token 来源：Authorization: Bearer <token> 或 cookie `webui_session`（HttpOnly 同源）。
  - 轮换 auth_secret 即吊销全部旧 token（无状态，无服务端会话表，省内存）。
"""

from __future__ import annotations

import time

from fastapi import HTTPException, Request, status

from ...core import config as config_module
from .token import TokenError, issue, verify

SESSION_COOKIE = "webui_session"


def _settings():
    """运行时从 config 模块读 settings（便于测试替换实例；生产为模块级单例）。"""
    return config_module.settings


class AuthPrincipal:
    """已通过鉴权的操作主体。后续 audit_log 用其记录『谁改了什么』。"""

    def __init__(self, subject: str = "admin") -> None:
        self.subject = subject


def _extract_token(request: Request) -> str | None:
    """从 Authorization 头或 cookie 取 token。都没有返回 None。

    优先 Authorization（API 用）；回退 cookie（浏览器同源用）。
    """
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie
    return None


async def require_auth(request: Request) -> AuthPrincipal:
    """依赖：校验请求鉴权。未配置密钥→503；缺失/无效 token→401；有效→返回主体。

    401 用标准『未认证』语义，不向外区分『token 缺失』与『token 错误』（防探测）。
    """
    settings = _settings()
    if not settings.auth_configured:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="鉴权未配置（WEBUI_AUTH_SECRET 为空），写接口拒绝裸奔。"
            "请在环境变量注入强随机密钥后再启用。",
        )
    token = _extract_token(request)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        subject = verify(token=token, secret=settings.auth_secret, now=time.time())
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="会话无效或已过期"
        ) from exc
    return AuthPrincipal(subject=subject)


async def require_auth_optional(request: Request) -> AuthPrincipal | None:
    """依赖：可选鉴权。用于只读接口——未登录可看，登录则主体可用于 audit。

    未配密钥或无 token → None（不抛）。有 token 但无效 → 仍 None（只读不强制，但坏 token 不计数）。
    """
    settings = _settings()
    if not settings.auth_configured:
        return None
    token = _extract_token(request)
    if not token:
        return None
    try:
        subject = verify(token=token, secret=settings.auth_secret, now=time.time())
    except TokenError:
        return None
    return AuthPrincipal(subject=subject)


__all__ = [
    "AuthPrincipal",
    "require_auth",
    "require_auth_optional",
    "issue",
    "verify",
    "TokenError",
]
