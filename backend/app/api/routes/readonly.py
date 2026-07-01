"""只读查看路由 —— 第一期：画像/episode 只读 + /epk 可视化；第二期：episode 删除。

复用 everos_gateway 的只读能力（get/search/health）。所有只读接口鉴权可选，
user_id 经 identity_resolver 校验（铁律：后端受信，不接受前端自由指定拼路径）。

响应信封统一约定：profile/episodes/search 三端点一律返回顶层 {data, meta}——
  data：主载荷（画像对象 / episode 列表 / EverOS 检索原始结构），无则 null。
  meta：分页等元数据（仅 episodes 用 {total, page, page_size}），无则 null。
（/health 是状态探针非资源读，仍返 {healthy}，不套此信封。）

episode 删除三端点（第二期，docs/06 §5/§8）：
  GET  .../plan    —— dry-run 预览，零写入，鉴权同写操作（预览属删除工作流的一环）。
  DELETE ...       —— 真正执行；先查 gateway.is_everos_stopped()，未停机→409 并给
                      SSH 停机指引（WebUI 不代 stop/start，见 docs/06 已定决策）。
  POST .../reindex/{txn} —— incremental 模式 apply 后、admin 重启 everos 完成，
                      手动触发对该 txn 的强制 cascade sync。
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ...core.constants import DEFAULT_APP_ID, DEFAULT_PROJECT_ID
from ...core.time import now_iso
from ...modules.audit_log import AuditEntry, AuditLog
from ...modules.auth import AuthPrincipal, require_auth, require_auth_optional
from ...modules.episode_editor import EpisodeEditor, ReindexMode, strip_owner_prefix
from ...modules.everos_gateway import EverOSGateway, EverOSUnavailable
from ...modules.identity_resolver import IdentityResolutionError
from ...modules.identity_resolver import resolve as resolve_identity
from ..deps import get_audit_log, get_episode_editor, get_gateway

router = APIRouter(prefix="/view")


class IdentityQuery(BaseModel):
    user_id: str
    app_id: str = DEFAULT_APP_ID
    project_id: str = DEFAULT_PROJECT_ID


@router.get("/health")
async def health(gateway: EverOSGateway = Depends(get_gateway)) -> dict:
    """EverOS 健康检查（对应 /epk status 的连通性部分）。"""
    ok = await gateway.health()
    return {"healthy": ok}


@router.get("/profile/{user_id}")
async def view_profile(
    user_id: str,
    app_id: str = Query(DEFAULT_APP_ID),
    project_id: str = Query(DEFAULT_PROJECT_ID),
    gateway: EverOSGateway = Depends(get_gateway),
    principal: AuthPrincipal | None = Depends(require_auth_optional),
) -> dict:
    """只读查看指定用户画像。{data: 画像对象或 null, meta: null}。"""
    try:
        ident = resolve_identity(user_id, app_id=app_id, project_id=project_id)
    except IdentityResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    try:
        profile = await gateway.get_profile(
            user_id=ident.user_id, app_id=ident.app_id, project_id=ident.project_id
        )
    except EverOSUnavailable as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return {"data": asdict(profile) if profile else None, "meta": None}


@router.get("/episodes/{user_id}")
async def view_episodes(
    user_id: str,
    app_id: str = Query(DEFAULT_APP_ID),
    project_id: str = Query(DEFAULT_PROJECT_ID),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    gateway: EverOSGateway = Depends(get_gateway),
    principal: AuthPrincipal | None = Depends(require_auth_optional),
) -> dict:
    """分页只读查看 episode。{data: episode 列表, meta: {total, page, page_size}}。"""
    try:
        ident = resolve_identity(user_id, app_id=app_id, project_id=project_id)
    except IdentityResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    try:
        items, total = await gateway.list_episodes(
            user_id=ident.user_id,
            app_id=ident.app_id,
            project_id=ident.project_id,
            page=page,
            page_size=page_size,
        )
    except EverOSUnavailable as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return {
        "data": [asdict(i) for i in items],
        "meta": {"total": total, "page": page, "page_size": page_size},
    }


@router.post("/search")
async def search(
    body: IdentityQuery,
    query: str = Query(..., description="检索词"),
    top_k: int = Query(5, ge=1, le=20),
    gateway: EverOSGateway = Depends(get_gateway),
    principal: AuthPrincipal | None = Depends(require_auth_optional),
) -> dict:
    """检索预览（对应 /epk search）。{data: EverOS 原始检索结构, meta: null}。"""
    try:
        ident = resolve_identity(body.user_id, app_id=body.app_id, project_id=body.project_id)
    except IdentityResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    try:
        data = await gateway.search(
            query=query,
            user_id=ident.user_id,
            app_id=ident.app_id,
            project_id=ident.project_id,
            top_k=top_k,
        )
    except EverOSUnavailable as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e)) from e
    return {"data": data, "meta": None}


# ── episode 删除（第二期，包 Palimpsest）───────────────────────────


class ReindexModeBody(BaseModel):
    reindex_mode: ReindexMode = "incremental"


def _resolved_owner_and_entry(user_id: str, entry_id: str) -> tuple[str, str]:
    """校验 user_id（三铁律）+ 剥离复合 entry_id 前缀。两处失败都是 400——
    前端传参错误，不是服务端故障。"""
    try:
        ident = resolve_identity(user_id)
    except IdentityResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    try:
        bare_entry_id = strip_owner_prefix(entry_id, ident.user_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return ident.user_id, bare_entry_id


@router.get("/episodes/{user_id}/{entry_id}/plan")
async def plan_delete_episode(
    user_id: str,
    entry_id: str,
    editor: EpisodeEditor = Depends(get_episode_editor),
    principal: AuthPrincipal = Depends(require_auth),
) -> dict:
    """dry-run 预览：目标 entry 是否存在、会改哪个文件。零写入。

    鉴权同删除本身（require_auth）——预览是删除工作流的一环，不是独立只读查看。
    """
    owner_id, bare_entry_id = _resolved_owner_and_entry(user_id, entry_id)
    preview = editor.preview_delete(owner_id=owner_id, entry_id=bare_entry_id)
    return {"data": asdict(preview), "meta": None}


@router.delete("/episodes/{user_id}/{entry_id}")
async def delete_episode(
    user_id: str,
    entry_id: str,
    body: ReindexModeBody = Body(default=ReindexModeBody()),
    editor: EpisodeEditor = Depends(get_episode_editor),
    gateway: EverOSGateway = Depends(get_gateway),
    audit_log: AuditLog = Depends(get_audit_log),
    principal: AuthPrincipal = Depends(require_auth),
) -> dict:
    """真正删除一条 episode entry。

    前置：everos 必须已停机（admin 手动 SSH，WebUI 不代 stop/start，docs/06 已定
    决策）。未停机 → 409（可重试的冲突态，不是 400 参数错误也不是 500 服务端故障）。
    """
    owner_id, bare_entry_id = _resolved_owner_and_entry(user_id, entry_id)
    if not await gateway.is_everos_stopped():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="everos 仍在运行，请先 SSH 执行 sudo systemctl stop everos 再重试。",
        )
    try:
        result = editor.apply_delete(
            owner_id=owner_id, entry_id=bare_entry_id, reindex_mode=body.reindex_mode
        )
    except RuntimeError as e:
        # Palimpsest.apply() 自己的 _guard_stopped()（真机验证坐实：会在 systemctl
        # 不可用的环境下抛这个，而不是让上面的 gateway 检查完全兜住）和 hash-drift
        # 冲突守卫都是同一 RuntimeError——两者都是"当前不能安全写，重试即可"的
        # 冲突态，同 409，不是 500。
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
    if result.status == "noop":
        # 目标 entry 在 plan 阶段就已不存在（重复删除/已被其他操作删过）——
        # Palimpsest 的 noop 分支不落快照、不碰 .index，这里映射 404 而非 200，
        # 免得前端把「什么都没删」当成「删除成功」。
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"entry {bare_entry_id!r} 不存在或已被删除",
        )
    try:
        await audit_log.record(
            AuditEntry(
                at=now_iso(),
                actor=principal.subject,
                action="episode.delete",
                user_id=owner_id,
                detail={
                    "entry_id": bare_entry_id,
                    "txn": result.txn,
                    "reindex_mode": result.reindex_mode,
                },
                backup_path=None,  # 快照路径在 Palimpsest 侧的 journal 里，不复述
            )
        )
    except OSError:
        pass  # 审计落盘失败不应让已完成的删除报失败（同 covenant 路由的既定处理）
    return {"data": asdict(result), "meta": None}


@router.post("/episodes/{user_id}/reindex/{txn}")
async def reindex_episode_txn(
    user_id: str,
    txn: str,
    editor: EpisodeEditor = Depends(get_episode_editor),
    principal: AuthPrincipal = Depends(require_auth),
) -> dict:
    """incremental 模式 apply 后、admin 重启 everos 完成，手动同步该 txn 的索引。

    user_id 本端点不直接使用（Palimpsest 的 journal 按 txn 而非 owner 检索），
    仅用于路由路径与其余 episode 端点保持一致、并过一次身份校验（不接受未经
    校验的路径段落进日志/错误信息）。校验失败仍走本函数内的 try/except（本仓
    没有全局异常处理器，未捕获的 ValueError 子类会被 FastAPI 当成 500）。
    """
    try:
        resolve_identity(user_id)
    except IdentityResolutionError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    try:
        result = editor.reindex_incremental(txn)
    except FileNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    return {"data": result, "meta": None}
