"""FastAPI 路由共享依赖：从 app.state 取单例（covenant_store/audit_log/everos_gateway/
episode_editor）。

单例由 main.lifespan 启动时建一次；路由经 Depends 注入，避免每请求 new（AuditLog
将来加锁/缓存不泄漏，episode_editor 与只读路由共用同一个 gateway 实例可对账）。
"""

from __future__ import annotations

from fastapi import HTTPException, Request, status

from ..modules.audit_log import AuditLog
from ..modules.covenant_store import CovenantStore
from ..modules.episode_editor import EpisodeEditor
from ..modules.everos_gateway import EverOSGateway


def get_store(request: Request) -> CovenantStore:
    return request.app.state.covenant_store  # type: ignore[attr-defined]


def get_audit_log(request: Request) -> AuditLog:
    return request.app.state.audit_log  # type: ignore[attr-defined]


def get_gateway(request: Request) -> EverOSGateway:
    return request.app.state.everos_gateway  # type: ignore[attr-defined]


def get_episode_editor(request: Request) -> EpisodeEditor:
    """episode_editor 依赖 EVEROS_MEMORY_ROOT——未配置时 main.lifespan 不建实例，
    这里给清楚的 503（同 auth.require_auth 的"未配置即拒绝裸奔"风格），而不是
    让 FastAPI 在没有该依赖时抛出难懂的 AttributeError。
    """
    editor = getattr(request.app.state, "episode_editor", None)
    if editor is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="episode 编辑未配置（EVEROS_MEMORY_ROOT 为空），请先配置 md 数据根。",
        )
    return editor
