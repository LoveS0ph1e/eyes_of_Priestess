"""API 路由聚合。各路由文件按第一期职责分组，留第二三期扩展位。"""

from __future__ import annotations

from fastapi import APIRouter

from . import audit as audit_routes
from . import auth as auth_routes
from . import covenant as covenant_routes
from . import readonly as readonly_routes

router = APIRouter(prefix="/api")
router.include_router(auth_routes.router, tags=["auth"])
router.include_router(covenant_routes.router, tags=["covenant"])
router.include_router(readonly_routes.router, tags=["readonly"])
router.include_router(audit_routes.router, tags=["audit"])
