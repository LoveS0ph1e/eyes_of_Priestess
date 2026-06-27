"""只读查看路由 —— 第一期：画像/episode 只读 + /epk 可视化。

复用 everos_gateway 的只读能力（get/search/health）。所有接口鉴权可选（读），
user_id 经 identity_resolver 校验（铁律：后端受信，不接受前端自由指定拼路径）。

响应信封统一约定：profile/episodes/search 三端点一律返回顶层 {data, meta}——
  data：主载荷（画像对象 / episode 列表 / EverOS 检索原始结构），无则 null。
  meta：分页等元数据（仅 episodes 用 {total, page, page_size}），无则 null。
（/health 是状态探针非资源读，仍返 {healthy}，不套此信封。）
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from ...core.constants import DEFAULT_APP_ID, DEFAULT_PROJECT_ID
from ...modules.auth import AuthPrincipal, require_auth, require_auth_optional
from ...modules.everos_gateway import EverOSGateway, EverOSUnavailable
from ...modules.identity_resolver import IdentityResolutionError
from ...modules.identity_resolver import resolve as resolve_identity
from ..deps import get_gateway

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


# ── episode 编辑（第二期占位，第一期返回 501）─────────────────────


@router.delete("/episodes/{user_id}/{entry_id}")
async def delete_episode_placeholder(
    user_id: str,
    entry_id: str,
    principal: AuthPrincipal = Depends(require_auth),
) -> dict:
    """[第二期] 删除一条 episode。第一期未实现。

    挂 require_auth 而非 optional：删除即写操作，第二期实现时无需回头改鉴权、
    也免被当作写接口『可选鉴权』的错误模板抄走。
    """
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="episode 编辑属第二期")
