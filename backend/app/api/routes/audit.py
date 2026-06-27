"""审计日志查看路由 —— 只读列出最近写操作。"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, Query

from ...modules.audit_log import AuditLog
from ...modules.auth import AuthPrincipal, require_auth_optional
from ..deps import get_audit_log

router = APIRouter(prefix="/audit")


@router.get("/recent")
async def list_recent(
    limit: int = Query(50, ge=1, le=500),
    audit_log: AuditLog = Depends(get_audit_log),
    principal: AuthPrincipal | None = Depends(require_auth_optional),
) -> dict:
    """最近 N 条审计记录。"""
    entries = await audit_log.list_recent(limit=limit)
    return {"items": [asdict(e) for e in entries]}
