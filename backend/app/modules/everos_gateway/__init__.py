"""EverOS 网关 —— 封装对 EverOS 的所有交互。

**唯一与 EverOS 私有格式耦合的地方**。隔离在此，便于 EverOS 升级时集中改。

读：走 HTTP API（get/search/health）—— EverOS 应用层裸奔，但只读风险低。
写/改/删：EverOS 无写 API，唯一路径 = 直接改磁盘 markdown + `everos cascade sync <path>`
          重建向量索引。

二期扩写（docs/06 §5，决策已定）：ABC 从只读 4 方法扩到 8 方法，新增
read_user_markdown / write_user_markdown / cascade_sync / is_everos_stopped。
本 ABC 面向 **WebUI 路由消费**（episode_editor 等调用方按 user_id/kind/date 高层身份
操作，不感知磁盘目录格式）；这与 Palimpsest（F:/Amadeus/Palimpsest/palimpsest.py，
面向批处理引擎，按 Selector 扫多文件）定位不同，两者独立实现、不共享类型或调用约定
——同一份"停机原子写"不变量，两套不同粒度的接口。

md 路径解析（kind+user_id+date → 磁盘路径）据 everos 包源码读码坐实（非猜测，见
`_resolve_md_path`），与 Palimpsest 的 `KIND_GLOBS` 相互印证一致。写操作用乐观锁
（`expected_sha256` 不符 → `MarkdownVersionConflict`，路由层捕获后应返回 409）。
`cascade_sync`/`is_everos_stopped` 都是只读探测或单点触发，绝不代为 stop/start
everos（docs/06 §5 决策：停/启由 admin SSH 手动，WebUI 不持 sudo）。

契约坐实（B5 真机只读 curl + 插件 core/everos_client.py）：
  信封 {request_id, data:{...}}；data 内 profiles/episodes/total_count 等。
  HTTPEverOSGateway 镜像插件 client 的 _post/health 语义，不重造。

环境约束（everos-windows-unsupported）：EverOS 用 fcntl.flock，Windows 原生跑不了。
  开发期用 Mock（EVEROS_GATEWAY=mock）；真机部署用 HTTPEverOSGateway。本模块不
  `import everos`——那会在 Windows 上于模块加载期就因 fcntl 崩溃，且引入
  lancedb/sqlmodel 等仅 Linux 部署需要的重依赖。md 读写/路径解析在此纯 stdlib
  重实现（同 Palimpsest 的选择），只与磁盘约定耦合，不与 everos 包耦合。
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import os
import subprocess
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

# ── 数据传输对象（与 EverOS 私有格式解耦的稳定边界）──────────────────
#
# 字段据 B5 真机返回坐实：
#   profile: data.profiles[0].profile_data.{summary, explicit_info[], implicit_traits[]}
#     explicit_info 项 = {category, description, evidence}
#     implicit_traits 项 = {trait, description, evidence, basis}
#   episode: data.episodes[0].{id, summary, subject, episode, timestamp, session_id, ...}


@dataclass(frozen=True)
class ProfileDTO:
    """画像只读视图。explicit/implicit 保结构化对象（不拍扁）。"""

    user_id: str
    summary: str
    explicit: list[dict[str, Any]]  # [{category, description, evidence}]
    implicit: list[dict[str, Any]]  # [{trait, description, evidence, basis}]
    raw: dict[str, Any] = field(default_factory=dict)  # 原始 profiles[0] 全量，供 diff/快照


@dataclass(frozen=True)
class EpisodeDTO:
    """episode 只读视图。entry_id 映射真机 id；全文/session/sender/score 等放 raw。"""

    entry_id: str
    summary: str
    subject: str
    timestamp: str
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MarkdownDocument:
    """一次 md 读/写的结果：全文 + sha256（乐观锁 version 令牌，docs/06 §6）。

    path 透传绝对路径字符串，供调用方原样传给 cascade_sync——不需重新解析一遍
    kind/user_id/date（gateway 内部已解析一次，避免两处路径拼接逻辑分裂）。
    """

    path: str
    text: str
    sha256: str


@dataclass(frozen=True)
class CascadeSyncResult:
    """单次 `everos cascade sync <path>` 调用结果（供路由层/前端展示原始输出）。"""

    ok: bool
    stdout: str
    stderr: str


# ── 抽象网关接口（二期：只读 4 + md 读写/cascade/停机探测 4，共 8 方法）──────


class EverOSGateway(ABC):
    """对 EverOS 的所有交互归口。

    实现二选一：
      - HTTPEverOSGateway：读走 HTTP（httpx 调 :8596）；md 读写/cascade 走本地文件
        + 子进程，镶嵌插件 everos_client 与 everos CLI 的既有契约，不重实现协议。
      - MockEverOSGateway：开发期桩（Windows 无 EverOS）。
    """

    @abstractmethod
    async def health(self) -> bool:
        """GET /health。任何异常视为不健康（沿用插件 everos_client 语义）。"""

    @abstractmethod
    async def get_profile(self, *, user_id: str, app_id: str, project_id: str) -> ProfileDTO | None:
        """取指定用户画像（只读）。无画像返回 None。"""

    @abstractmethod
    async def list_episodes(
        self, *, user_id: str, app_id: str, project_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[EpisodeDTO], int]:
        """分页列举 episode，返回 (条目列表, 总数)。"""

    @abstractmethod
    async def search(
        self, *, query: str, user_id: str, app_id: str, project_id: str, top_k: int = 5
    ) -> dict[str, Any]:
        """检索（注入预览/debug 用）。返回 EverOS 原始 data 结构。"""

    @abstractmethod
    async def read_user_markdown(
        self,
        *,
        user_id: str,
        kind: str,
        app_id: str,
        project_id: str,
        date: _dt.date | None = None,
    ) -> MarkdownDocument | None:
        """读取指定用户+kind(+date)的 md 全文+sha256。

        kind ∈ {profile, episode, atomic_fact, foresight}；profile 是单文件，
        无 date；其余三种是按日分片的 daily-log，date 必填（未给 → ValueError）。
        文件不存在 → None（不是错误，调用方据此决定是"新建"还是"编辑"）。
        """

    @abstractmethod
    async def write_user_markdown(
        self,
        *,
        user_id: str,
        kind: str,
        app_id: str,
        project_id: str,
        new_text: str,
        expected_sha256: str,
        date: _dt.date | None = None,
    ) -> MarkdownDocument:
        """乐观锁整份原子写。

        expected_sha256 须等于写前磁盘现态的 sha256（新建文件传空文本的哈希）；
        不符 → MarkdownVersionConflict（路由层应映射 409，前端重拉重试，docs/06 §6）。
        本方法只做整份文本替换，不解析 entry 结构——entry 级操作（如删单条
        episode）由调用方（episode_editor）先 read 现态、正则改出 new_text，
        再连同当时读到的 sha256 一起传回来。
        """

    @abstractmethod
    async def cascade_sync(self, path: str) -> CascadeSyncResult:
        """对单个 md 路径强制入队 + drain（`everos cascade sync <path>`）。

        incremental 重索引路径用（docs/06 §5/§8，对应 Palimpsest 的
        reindex-incremental 步骤，但这里是单文件即时调用，不是 txn 批量收尾）。
        path 通常是 read/write_user_markdown 返回的 MarkdownDocument.path。
        """

    @abstractmethod
    async def is_everos_stopped(self) -> bool:
        """只读探测 everos 是否已停机（`systemctl is-active` != active）。

        仅用于 WebUI 自检 + 给 admin 停机指引；绝不代为 stop/start（docs/06 §5
        决策：WebUI 不持 sudo/SSH 私钥，停/启由 admin SSH 手动）。无法确认时
        （非 systemd 环境等）保守返回 False——调用方不应在"不确定"时误判为已停机
        而继续走停机专属操作。
        """


class EverOSUnavailable(Exception):
    """EverOS 不可达 / 请求失败 / 返回 error 包络（沿用插件同名异常语义）。"""


class MarkdownVersionConflict(Exception):
    """write_user_markdown 的 expected_sha256 与磁盘现态不符（并发修改）。"""


class EverOSGatewayMisconfigured(Exception):
    """md 读写/cascade 被调用，但网关未配置 memory_root（EVEROS_MEMORY_ROOT 留空）。"""


# ── md 路径解析 + 原子写辅助（纯 stdlib，不 import everos，见模块头约束）───
#
# 目录/文件名约定据 everos 包源码读码坐实（core/persistence/memory_root.py 的
# app_dir_name/project_dir_name；infra/persistence/markdown/mds/{episode,
# atomic_fact,foresight,profile}.py 的 DIR_NAME/FILE_PREFIX/PROFILE_FILENAME），
# 与 Palimpsest 的 KIND_GLOBS 相互印证一致——两处独立实现、结论相同，非抄一处。

_MD_KIND_LAYOUT: dict[str, tuple[str | None, str | None]] = {
    # kind -> (dir_name, file_prefix)。dir_name=None 表示单文件、无日期分片。
    "profile": (None, None),
    "episode": ("episodes", "episode"),
    "atomic_fact": (".atomic_facts", "atomic_fact"),
    "foresight": (".foresights", "foresight"),
}


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _app_dir_name(app_id: str) -> str:
    return "default_app" if app_id == "default" else app_id


def _project_dir_name(project_id: str) -> str:
    return "default_project" if project_id == "default" else project_id


def _resolve_md_path(
    memory_root: Path,
    *,
    user_id: str,
    kind: str,
    app_id: str,
    project_id: str,
    date: _dt.date | None,
) -> Path:
    """kind(+date) → 磁盘绝对路径。见本节头注：约定读码坐实，不 import everos。"""
    if kind not in _MD_KIND_LAYOUT:
        raise ValueError(f"unknown kind: {kind!r}")
    user_dir = (
        memory_root / _app_dir_name(app_id) / _project_dir_name(project_id) / "users" / user_id
    )
    dir_name, file_prefix = _MD_KIND_LAYOUT[kind]
    if dir_name is None:  # profile：单文件，无日期
        return user_dir / "user.md"
    if date is None:
        raise ValueError(f"kind={kind!r} 是按日分片，date 不可省略")
    return user_dir / dir_name / f"{file_prefix}-{date.isoformat()}.md"


def _read_text_if_exists(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def _atomic_write_text(path: Path, content: str) -> None:
    """temp + os.replace 原子写，同目录保证同文件系统（镜像 everos MarkdownWriter /
    Palimpsest 的原子写惯例：读码坐实，非猜测）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp.{uuid.uuid4().hex}"
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


def _relative_to_root(path: Path, root: Path) -> str:
    """cascade sync 要求相对 memory_root 的路径（md_change_state 存储惯例，
    见 everos cascade.py `_resolve_relative` / Palimpsest `_relative_to_root`）。"""
    return path.resolve().relative_to(root.resolve()).as_posix()


# ── 真实网关（HTTP，镜像插件 everos_client 契约）──────────────────


def _to_profile_dto(p: dict[str, Any]) -> ProfileDTO:
    """把真机 profiles[0] 映射成 ProfileDTO。三字段在 profile_data 子对象里。"""
    pdata = p.get("profile_data") or {}
    return ProfileDTO(
        user_id=str(p.get("user_id", "")),
        summary=pdata.get("summary", "") or "",
        explicit=list(pdata.get("explicit_info") or []),
        implicit=list(pdata.get("implicit_traits") or []),
        raw=p,
    )


def _to_episode_dto(e: dict[str, Any]) -> EpisodeDTO:
    """把真机 episodes[0] 映射成 EpisodeDTO。entry_id=id；全量进 raw。"""
    return EpisodeDTO(
        entry_id=str(e.get("id", "")),
        summary=e.get("summary", "") or "",
        subject=e.get("subject", "") or "",
        timestamp=e.get("timestamp", "") or "",
        raw=e,
    )


class HTTPEverOSGateway(EverOSGateway):
    """读走 HTTP 调 EverOS :8596。镜像插件 core/everos_client.py 的 _post/health 语义。

    httpx.AsyncClient 单例（ASGI 安全复用），lifespan 起时建、停时 aclose。

    memory_root 留空（EVEROS_MEMORY_ROOT 未配置）时只读 4 方法照常工作；
    md 读写/cascade_sync 调用会抛 EverOSGatewayMisconfigured——第一期部署不碰 md，
    留空是合法状态，不该在构造时就报错拒绝启动。
    """

    def __init__(
        self,
        base_url: str,
        timeout: float = 90.0,
        *,
        memory_root: Path | None = None,
        everos_bin: str = "everos",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)
        self.memory_root = memory_root
        # 真机坐实（SSH 验证）：`everos` 装在 venv 内
        # （~/everos/.venv/bin/everos），不在登录 shell 或 systemd 服务的 PATH 里
        # ——裸命令会 subprocess FileNotFoundError。默认仍留裸名（兼容已把 venv
        # 加进 PATH 的环境），生产经 EVEROS_BIN 配置全路径。
        self.everos_bin = everos_bin
        # md 读写在 read-modify-write 窗口内跨 await（读现态 sha256 → 校验 → 写），
        # 并发请求同路径必须串行，否则后写者可能踩着前写者未提交的中间态判断乐观锁
        # （TOCTOU）。per-path asyncio.Lock，镜像 everos MarkdownWriter.lock_for 的
        # 设计（读码坐实）；锁绑定本实例所在事件循环，与该模块头注一致。
        self._path_locks: dict[Path, asyncio.Lock] = {}

    def _lock_for(self, path: Path) -> asyncio.Lock:
        key = path.resolve()
        lock = self._path_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._path_locks[key] = lock
        return lock

    def _require_memory_root(self) -> Path:
        if self.memory_root is None:
            raise EverOSGatewayMisconfigured(
                "EVEROS_MEMORY_ROOT 未配置，无法读写 md / 触发 cascade sync"
            )
        return self.memory_root

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        """统一 POST：发请求 → raise_for_status → 取 data。

        网络/HTTP 错误统一抛 EverOSUnavailable；error 包络也抛（复刻插件 _post）。
        """
        try:
            resp = await self._client.post(f"{self.base_url}{path}", json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise EverOSUnavailable(f"POST {path} 失败: {e}") from e
        body = resp.json()
        if isinstance(body, dict) and "error" in body:
            err = body["error"]
            msg = err.get("message", "unknown") if isinstance(err, dict) else str(err)
            raise EverOSUnavailable(f"EverOS error @ {path}: {msg}")
        return body.get("data", {}) if isinstance(body, dict) else {}

    async def health(self) -> bool:
        try:
            resp = await self._client.get(f"{self.base_url}/health")
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError):
            return False
        status = body.get("status")
        if status is None and isinstance(body.get("data"), dict):
            status = body["data"].get("status")
        return status in ("ok", "healthy")

    async def get_profile(self, *, user_id: str, app_id: str, project_id: str) -> ProfileDTO | None:
        data = await self._post(
            "/api/v1/memory/get",
            {
                "memory_type": "profile",
                "app_id": app_id,
                "project_id": project_id,
                "page": 1,
                "page_size": 20,
                "sort_by": "timestamp",
                "sort_order": "desc",
                "user_id": user_id,
            },
        )
        profiles = data.get("profiles") or []
        if not profiles:
            return None
        return _to_profile_dto(profiles[0])

    async def list_episodes(
        self, *, user_id: str, app_id: str, project_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[EpisodeDTO], int]:
        data = await self._post(
            "/api/v1/memory/get",
            {
                "memory_type": "episode",
                "app_id": app_id,
                "project_id": project_id,
                "page": page,
                "page_size": page_size,
                "sort_by": "timestamp",
                "sort_order": "desc",
                "user_id": user_id,
            },
        )
        episodes = [_to_episode_dto(e) for e in (data.get("episodes") or [])]
        total = int(data.get("total_count", len(episodes)))
        return episodes, total

    async def search(
        self, *, query: str, user_id: str, app_id: str, project_id: str, top_k: int = 5
    ) -> dict[str, Any]:
        return await self._post(
            "/api/v1/memory/search",
            {
                "query": query,
                "app_id": app_id,
                "project_id": project_id,
                "method": "hybrid",
                "top_k": top_k,
                "include_profile": False,
                "enable_llm_rerank": False,
                "user_id": user_id,
            },
        )

    # ── md 读写（二期，直接磁盘 IO，不走 HTTP——EverOS 无写 API）──────

    async def read_user_markdown(
        self,
        *,
        user_id: str,
        kind: str,
        app_id: str,
        project_id: str,
        date: _dt.date | None = None,
    ) -> MarkdownDocument | None:
        root = self._require_memory_root()
        path = _resolve_md_path(
            root, user_id=user_id, kind=kind, app_id=app_id, project_id=project_id, date=date
        )
        async with self._lock_for(path):
            text = _read_text_if_exists(path)
        if text is None:
            return None
        return MarkdownDocument(path=str(path), text=text, sha256=_sha256(text))

    async def write_user_markdown(
        self,
        *,
        user_id: str,
        kind: str,
        app_id: str,
        project_id: str,
        new_text: str,
        expected_sha256: str,
        date: _dt.date | None = None,
    ) -> MarkdownDocument:
        root = self._require_memory_root()
        path = _resolve_md_path(
            root, user_id=user_id, kind=kind, app_id=app_id, project_id=project_id, date=date
        )
        async with self._lock_for(path):
            current = _read_text_if_exists(path) or ""
            if _sha256(current) != expected_sha256:
                raise MarkdownVersionConflict(
                    f"{path} 已被其他操作修改（expected={expected_sha256[:12]}，"
                    f"actual={_sha256(current)[:12]}），请重新读取后再试"
                )
            _atomic_write_text(path, new_text)
        return MarkdownDocument(path=str(path), text=new_text, sha256=_sha256(new_text))

    async def cascade_sync(self, path: str) -> CascadeSyncResult:
        root = self._require_memory_root()
        rel = _relative_to_root(Path(path), root)
        proc = await asyncio.to_thread(
            subprocess.run,
            [self.everos_bin, "cascade", "sync", rel],
            capture_output=True,
            text=True,
        )
        return CascadeSyncResult(
            ok=proc.returncode == 0,
            stdout=proc.stdout.strip(),
            stderr=proc.stderr.strip(),
        )

    async def is_everos_stopped(self) -> bool:
        try:
            proc = await asyncio.to_thread(
                subprocess.run,
                ["systemctl", "is-active", "everos"],
                capture_output=True,
                text=True,
            )
        except (OSError, FileNotFoundError):
            # 非 systemd 环境（如本机开发/容器）无法确认——保守返回 False，
            # 绝不让"无法确认"被调用方误当作"已停机"进而跳过安全检查。
            return False
        return proc.stdout.strip() != "active"


# ── 开发期 mock 占位 ──────────────────────────────────────────────


class MockEverOSGateway(EverOSGateway):
    """开发期 mock：Windows 无 EverOS（fcntl），用此桩跑通路由骨架。

    只读方法返回空安全值；md 读写方法用进程内 dict 模拟磁盘状态（可读写、可乐观锁
    冲突，供 episode_editor 等上层在 Windows 开发机上跑通整条流程，不只是空转）。
    真机部署换 HTTPEverOSGateway。
    """

    def __init__(self) -> None:
        self._md_store: dict[tuple[str, str, str], str] = {}

    def _md_key(
        self, *, user_id: str, kind: str, app_id: str, project_id: str, date: _dt.date | None
    ) -> tuple[str, str, str]:
        date_part = date.isoformat() if date else ""
        return (f"{app_id}/{project_id}/{user_id}", kind, date_part)

    async def health(self) -> bool:
        return True

    async def get_profile(self, *, user_id: str, app_id: str, project_id: str) -> ProfileDTO | None:
        return None

    async def list_episodes(
        self, *, user_id: str, app_id: str, project_id: str, page: int = 1, page_size: int = 20
    ) -> tuple[list[EpisodeDTO], int]:
        return [], 0

    async def search(
        self, *, query: str, user_id: str, app_id: str, project_id: str, top_k: int = 5
    ) -> dict[str, Any]:
        return {"episodes": [], "profiles": []}

    async def read_user_markdown(
        self,
        *,
        user_id: str,
        kind: str,
        app_id: str,
        project_id: str,
        date: _dt.date | None = None,
    ) -> MarkdownDocument | None:
        if kind not in _MD_KIND_LAYOUT:
            raise ValueError(f"unknown kind: {kind!r}")
        if _MD_KIND_LAYOUT[kind][0] is not None and date is None:
            raise ValueError(f"kind={kind!r} 是按日分片，date 不可省略")
        key = self._md_key(
            user_id=user_id, kind=kind, app_id=app_id, project_id=project_id, date=date
        )
        text = self._md_store.get(key)
        if text is None:
            return None
        return MarkdownDocument(path=f"mock://{'/'.join(key)}", text=text, sha256=_sha256(text))

    async def write_user_markdown(
        self,
        *,
        user_id: str,
        kind: str,
        app_id: str,
        project_id: str,
        new_text: str,
        expected_sha256: str,
        date: _dt.date | None = None,
    ) -> MarkdownDocument:
        if kind not in _MD_KIND_LAYOUT:
            raise ValueError(f"unknown kind: {kind!r}")
        if _MD_KIND_LAYOUT[kind][0] is not None and date is None:
            raise ValueError(f"kind={kind!r} 是按日分片，date 不可省略")
        key = self._md_key(
            user_id=user_id, kind=kind, app_id=app_id, project_id=project_id, date=date
        )
        current = self._md_store.get(key, "")
        if _sha256(current) != expected_sha256:
            raise MarkdownVersionConflict(
                f"mock://{'/'.join(key)} 已被其他操作修改（expected={expected_sha256[:12]}，"
                f"actual={_sha256(current)[:12]}），请重新读取后再试"
            )
        self._md_store[key] = new_text
        new_sha = _sha256(new_text)
        return MarkdownDocument(path=f"mock://{'/'.join(key)}", text=new_text, sha256=new_sha)

    async def cascade_sync(self, path: str) -> CascadeSyncResult:
        return CascadeSyncResult(ok=True, stdout=f"mock sync: {path}", stderr="")

    async def is_everos_stopped(self) -> bool:
        # 开发期 mock 场景没有真实 everos 进程——恒当作"已停机"，让上层
        # 停机专属操作（如 apply 前置检查）在本机也能跑通整条流程。
        return True
