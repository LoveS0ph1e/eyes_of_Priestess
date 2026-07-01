"""FastAPI 应用入口。

启动：uvicorn app.main:app --host 127.0.0.1 --port 8761
⚠️ host 必须是 127.0.0.1，绝不 0.0.0.0（EverOS 应用层裸奔，写记忆入口禁公网）。

CORS：仅放行前端开发服务器（默认 http://127.0.0.1:5173）。绝不 allow_origins=["*"]
（EverOS 那种裸奔正是本服务要避免的反面教材）。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .api.routes import router as api_router
from .core import config as config_module
from .core.config import Settings
from .modules.audit_log import AuditLog
from .modules.covenant_store import CovenantStore
from .modules.episode_editor import EpisodeEditor
from .modules.everos_gateway import HTTPEverOSGateway, MockEverOSGateway


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：启动时校验硬约束、初始化单例，停机时清理资源。

    单例下 builders 放 app.state：CovenantStore（文件读写，无状态，但共享路径）/ AuditLog
    （将来加锁/缓存）/ EverOSGateway（mock 第一期，第二期真机置换）。各路由经 Depends
    注入同实例，避免每请求 new——第二期 episode_editor 也要 gateway，多路由对账靠此。
    """
    # 启动守卫：网络边界自检（生产环境必须绑 127.0.0.1）。
    # 运行时从 config 模块实时读 settings，便于测试替换实例；生产用模块级单例。
    settings: Settings = config_module.settings
    if settings.host not in ("127.0.0.1", "localhost", "::1"):
        raise RuntimeError(
            f"host={settings.host!r} 违反安全边界：必须绑 127.0.0.1。"
            "EverOS 应用层裸奔，WebUI 写记忆入口绝不公网。"
        )
    # 单例初始化。AuditLog 目录在 record 时 mkdir，此处只建实例不创建文件。
    app.state.covenant_store = CovenantStore(settings.plugin_config_path)
    app.state.audit_log = AuditLog(Path(settings.audit_log_path))
    # memory_root 留空（第一期默认）是合法状态：只读 4 方法不需要它，md 读写/
    # cascade_sync/episode_editor 调用时才会因未配置而报错（见 everos_gateway 模块头）。
    memory_root = Path(settings.everos_memory_root) if settings.everos_memory_root else None
    # 网关：EVEROS_GATEWAY=http 用真网关连 :8596，否则 mock（开发期 Windows 无 EverOS）。
    if settings.everos_gateway == "http":
        # EVEROS_BIN 留空 → 用网关构造函数自己的裸名默认值（"everos"，兼容已把
        # venv 加进 PATH 的环境）；配了才覆盖成全路径（生产按真机 PATH 实测配置）。
        gateway_kwargs = {"everos_bin": settings.everos_bin} if settings.everos_bin else {}
        app.state.everos_gateway = HTTPEverOSGateway(
            settings.everos_base_url,
            timeout=settings.everos_timeout,
            memory_root=memory_root,
            **gateway_kwargs,
        )
    else:
        app.state.everos_gateway = MockEverOSGateway()
    # episode_editor（Palimpsest 适配层）同样要 memory_root，留空则不建实例——
    # deps.get_episode_editor 会据此给清楚的 503，而非裸 AttributeError。
    # everos_bin 同一个配置值传给 gateway 和 episode_editor 两边——两套独立子
    # 进程调用（真机验证坐实：Palimpsest.reindex_incremental 内部另起一次
    # `everos cascade sync`），共享的是同一个"everos 装在哪"的事实，不共享代码。
    if memory_root:
        episode_editor_kwargs = {"everos_bin": settings.everos_bin} if settings.everos_bin else {}
        app.state.episode_editor = EpisodeEditor(memory_root, **episode_editor_kwargs)
    else:
        app.state.episode_editor = None
    yield
    # 停机清理：HTTP 网关关闭 httpx client（mock 无此方法）。
    gw = app.state.everos_gateway
    if isinstance(gw, HTTPEverOSGateway):
        await gw.aclose()


app = FastAPI(
    title="ReadingSteiner 记忆管理 WebUI",
    description="基于 EverOS 自进化记忆引擎的记忆运维 WebUI（独立服务，分期按耦合度递进）",
    version="0.2.0",
    lifespan=lifespan,
)

# CORS：白名单，绝不 *。前端开发服务器默认 5173；生产同源或经 SSH 隧道。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)

app.include_router(api_router)


@app.get("/health")
async def root_health() -> dict:
    """本服务自身健康检查（区别于 /api/view/health 的 EverOS 健康）。"""
    return {"status": "ok", "auth_configured": config_module.settings.auth_configured}


def _safe_spa_target(root: Path, full_path: str) -> Path | None:
    """把 SPA 请求路径解析到 build 内真实文件；不存在/越界返 None（调用方回退 index.html）。

    仿静态主机 ``try_files $uri $uri.html``：先试原路径、再试 ``{path}.html``（预渲染页）。
    路径穿越防护：解析后必须仍在 root 内，绝不发越界文件（同 docs/02 身份铁律的同源精神）。
    """
    if not full_path:
        return None
    for rel in (full_path, f"{full_path}.html"):
        target = (root / rel).resolve()
        if target.is_file() and target.is_relative_to(root):
            return target
    return None


# ── 前端静态托管（生产；开发期 WEBUI_FRONTEND_DIR 留空则走 vite dev server）─────────
# 设了 WEBUI_FRONTEND_DIR 且目录存在才挂载：前端与 /api 同源由一个 uvicorn 托管
# （免 CORS、单端口、最省内存，见 docs/05 决策）。仍绑 127.0.0.1（docs/02 不变）。
# 此 catch-all 注册在 include_router 与 /health 之后，故不遮挡既有 API 与健康检查。
_frontend_dir = Path(config_module.settings.frontend_dir or "")
if config_module.settings.frontend_dir and _frontend_dir.is_dir():
    _frontend_root = _frontend_dir.resolve()
    _index_html = _frontend_root / "index.html"

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str) -> FileResponse:
        """SPA 回退：命中文件/预渲染页就发，否则发 index.html（客户端路由）。

        /api/* 未命中仍返回 404，不被前端 index.html 吞掉。
        """
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not Found")
        target = _safe_spa_target(_frontend_root, full_path)
        return FileResponse(target if target else _index_html)
