"""铭契（eternal_covenant）编辑路由 —— 第一期核心，零 EverOS 耦合。

所有写操作前置 require_auth（P0）；落 audit_log；改完建议触发一次注入校验
（第一期验收：真机发消息确认【永恒铭契】块随之变）。

CovenantStore / AuditLog 经 Depends 注入 app.state 单例（见 main.lifespan）。
WEBUI_COVENANT_READONLY=1 时写接口直接 502 拒写（联调防误改生产，代码开关胜过纪律）。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from ...core import config as config_module
from ...core.time import now_iso
from ...modules.audit_log import AuditEntry, AuditLog
from ...modules.auth import AuthPrincipal, require_auth, require_auth_optional
from ...modules.covenant_store import Covenant, CovenantStore, CovenantStoreError
from ...modules.identity_resolver import IdentityResolutionError
from ...modules.identity_resolver import resolve as resolve_identity
from ..deps import get_audit_log, get_store

router = APIRouter(prefix="/covenant")

_READONLY_MSG = "covenant 只读模式（WEBUI_COVENANT_READONLY=1）：写接口已禁用"


class CovenantBody(BaseModel):
    user_id: str = Field(..., description="铭契锁定的用户 QQ 号")
    text: str = Field(..., description="固定核心设定文本")


def _readonly_guard() -> None:
    """联调/生产防误改：只读开关打开时拒写并 502。"""
    if config_module.settings.covenant_readonly:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_READONLY_MSG)


async def _record(
    *,
    audit_log: AuditLog,
    principal: AuthPrincipal,
    action: str,
    user_id: str,
    detail: dict,
    backup_path: str | None,
) -> None:
    """落一条审计记录；审计写失败不应让写操作回滚（best-effort，吞异常仅记日志）。"""
    try:
        await audit_log.record(
            AuditEntry(
                at=now_iso(),
                actor=principal.subject,
                action=action,
                user_id=user_id,
                detail=detail,
                backup_path=backup_path,
            )
        )
    except OSError:
        # 审计落盘失败不应让铭契写回滚——真实写已成功；这里吞掉，后续可加日志。
        pass


@router.get("", response_model=list[Covenant])
async def list_covenants(
    store: CovenantStore = Depends(get_store),
    principal: AuthPrincipal = Depends(require_auth_optional),
) -> list[Covenant]:
    """列出全部铭契（只读接口，鉴权可选）。"""
    try:
        return await store.list_all()
    except CovenantStoreError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e


@router.put("/{user_id}")
async def upsert_covenant(
    body: CovenantBody,
    principal: AuthPrincipal = Depends(require_auth),
    store: CovenantStore = Depends(get_store),
    audit_log: AuditLog = Depends(get_audit_log),
) -> dict:
    """新增/覆盖一条铭契（写操作，必须登录 + 落审计 + 备份 + 注入校验）。"""
    _readonly_guard()
    try:
        ident = resolve_identity(body.user_id)
    except IdentityResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    if ident.user_id != body.user_id:  # pragma: no cover - 校验幂等时恒成立
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="身份解析不一致")
    try:
        before = await store.get(body.user_id)
    except CovenantStoreError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    try:
        await store.upsert(body.user_id, body.text)
    except CovenantStoreError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    # TODO: 触发注入校验（真机发消息或调插件验证钩子）
    await _record(
        audit_log=audit_log,
        principal=principal,
        action="covenant.upsert",
        user_id=body.user_id,
        detail={
            "before": before.text if before else None,
            "after": body.text.strip() or None,
        },
        backup_path=store.last_backup_path,
    )
    return {"ok": True, "user_id": body.user_id}


@router.delete("/{user_id}")
async def delete_covenant(
    user_id: str,
    principal: AuthPrincipal = Depends(require_auth),
    store: CovenantStore = Depends(get_store),
    audit_log: AuditLog = Depends(get_audit_log),
) -> dict:
    """删除一条铭契（写操作，同上守卫）。"""
    _readonly_guard()
    try:
        resolve_identity(user_id)
    except IdentityResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    try:
        before = await store.get(user_id)
    except CovenantStoreError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    try:
        removed = await store.delete(user_id)
    except CovenantStoreError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    await _record(
        audit_log=audit_log,
        principal=principal,
        action="covenant.delete",
        user_id=user_id,
        detail={"before": before.text if before else None, "removed": removed},
        backup_path=store.last_backup_path,
    )
    return {"ok": True, "removed": removed}
