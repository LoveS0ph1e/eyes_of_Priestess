"""EverOS 网关 —— 封装对 EverOS 的所有交互。

**唯一与 EverOS 私有格式耦合的地方**。隔离在此，便于 EverOS 升级时集中改。

读：走 HTTP API（get/search/health）—— EverOS 应用层裸奔，但只读风险低。
写/改/删：EverOS 无写 API，唯一路径 = 直接改磁盘 markdown + `everos cascade sync <path>`
          重建向量索引。（第二期 episode_editor 落地时启用；第一期不碰。）

⚠️ 反过早抽象（karpathy）：本 ABC 第一期**只声明只读 4 方法**。写/cascade 方法
（read/write_user_markdown / cascade_sync）刻意不进 ABC——cascade 调用方式是 plan
「仍开放」项，未定即不固化签名。第二期决策定了再扩。

契约坐实（真机只读 curl + 插件 core/everos_client.py）：
  信封 {request_id, data:{...}}；data 内 profiles/episodes/total_count 等。
  HTTPEverOSGateway 镜像插件 client 的 _post/health 语义，不重造。

环境约束：EverOS 用 fcntl.flock，Windows 原生跑不了。
  开发期用 Mock（EVEROS_GATEWAY=mock）；真机部署用 HTTPEverOSGateway。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import httpx

# ── 数据传输对象（与 EverOS 私有格式解耦的稳定边界）──────────────────
#
# 字段据 真机返回坐实：
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


# ── 抽象网关接口（第一期：只读 4 方法）─────────────────────────────


class EverOSGateway(ABC):
    """对 EverOS 的只读交互归口（第一期）。

    实现二选一：
      - HTTPEverOSGateway：读走 HTTP（httpx 调 :8596），镜像插件 everos_client 契约。
      - MockEverOSGateway：开发期桩（Windows 无 EverOS）。
    写/cascade 路径第二三期再扩 ABC（见模块头『反过早抽象』）。
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


class EverOSUnavailable(Exception):
    """EverOS 不可达 / 请求失败 / 返回 error 包络（沿用插件同名异常语义）。"""


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
    """

    def __init__(self, base_url: str, timeout: float = 90.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=timeout)

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


# ── 开发期 mock 占位 ──────────────────────────────────────────────


class MockEverOSGateway(EverOSGateway):
    """开发期 mock：Windows 无 EverOS（fcntl），用此桩跑通路由骨架。

    只读方法返回空安全值。真机部署换 HTTPEverOSGateway。
    """

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
