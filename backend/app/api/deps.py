"""FastAPI 路由共享依赖：从 app.state 取单例（covenant_store/audit_log/everos_gateway）。

单例由 main.lifespan 启动时建一次；路由经 Depends 注入，避免每请求 new（AuditLog
将来加锁/缓存不泄漏，第二期 episode_editor 要 gateway 时各路由同实例可对账）。
"""

from __future__ import annotations

from fastapi import Request

from ..modules.audit_log import AuditLog
from ..modules.covenant_store import CovenantStore
from ..modules.everos_gateway import EverOSGateway


def get_store(request: Request) -> CovenantStore:
    return request.app.state.covenant_store  # type: ignore[attr-defined]


def get_audit_log(request: Request) -> AuditLog:
    return request.app.state.audit_log  # type: ignore[attr-defined]


def get_gateway(request: Request) -> EverOSGateway:
    return request.app.state.everos_gateway  # type: ignore[attr-defined]
