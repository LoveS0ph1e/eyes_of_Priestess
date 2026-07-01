"""episode 删除 —— 第二期首个落地能力（中耦合，需 cascade）。

机制：包 Palimpsest（backend/app/modules/palimpsest/）对单条 episode entry 做
DeleteEntry：定位 md 文件 → 删 `<!-- entry:id -->` 块 → 幂等维护 frontmatter
`entry_count` → 快照 → drop `.index`（full）或记录 `reindex_paths`（incremental）。

为什么不直接调 everos_gateway.write_user_markdown：那个方法只做「整份文本替换」，
entry 级删除的正则+frontmatter 维护逻辑 Palimpsest 已经写好、快照/回滚/journal
配套齐全，这里重新拿正则实现一遍是重复劳动（且 entry_count 维护要幂等，Palimpsest
的实现已用 smoke test 验证过边界，见 palimpsest/PALIMPSEST.md §1.1）。

owner_id vs entry_id：EverOS API 的复合 id 形如 `{owner_id}_{entry_id}`（见
everos_gateway._to_episode_dto 的 entry_id=e["id"]）；Palimpsest 的 DeleteEntry
要的是裸 entry_id（md 标记里的形式，如 `ep_20260625_00000008`）。本模块的
`strip_owner_prefix` 是这两种表示之间唯一做转换的地方，别在路由层重复实现。

停机检查：apply 前先调 gateway.is_everos_stopped() 给出清楚的错误信息（"请先
SSH 停机"），而不是让 Palimpsest 内部 `_guard_stopped()` 抛裸 subprocess 相关的
RuntimeError——那个错误信息面向 CLI 使用者，不适合直接透给 WebUI 用户。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ...core.constants import DEFAULT_APP_ID, DEFAULT_PROJECT_ID
from ..palimpsest import DeleteEntry, Layout, Palimpsest, Plan, Selector

ReindexMode = Literal["incremental", "full"]

# app_id/project_id 是固定契约常量（见 core/constants.py），不是可调参数——
# 与 everos_gateway 的 _app_dir_name/_project_dir_name 各自独立实现同一份磁盘
# 命名映射（"default" -> "default_app"/"default_project"），两处结论一致，
# 不是抄一处（同 everos_gateway 模块头对 KIND_GLOBS 的说明）。
_DISK_APP_ID = "default_app" if DEFAULT_APP_ID == "default" else DEFAULT_APP_ID
_DISK_PROJECT_ID = "default_project" if DEFAULT_PROJECT_ID == "default" else DEFAULT_PROJECT_ID


def strip_owner_prefix(composite_id: str, owner_id: str) -> str:
    """`{owner_id}_{entry_id}` → `entry_id`（EverOS API 复合 id → md 标记裸 id）。

    前缀不匹配说明调用方传错了 owner_id/entry_id 组合（用户信息不一致，属于
    调用方 bug，不该静默处理成"删除失败"之类的模糊结果）。
    """
    prefix = f"{owner_id}_"
    if not composite_id.startswith(prefix):
        raise ValueError(
            f"entry_id {composite_id!r} 不属于 owner_id {owner_id!r}（期望前缀 {prefix!r}）"
        )
    return composite_id[len(prefix) :]


@dataclass(frozen=True)
class EpisodeDeletePreview:
    """delete 前的 dry-run 预览（对应 Palimpsest Plan，去掉引擎内部字段）。"""

    plan_id: str
    is_empty: bool
    render: str


@dataclass(frozen=True)
class EpisodeDeleteResult:
    """apply 完成后的结果。reindex_mode 决定前端是否要提示"手动同步索引"。"""

    txn: str
    status: str
    reindex_mode: ReindexMode
    reindex_paths: list[str]


class EpisodeEditor:
    """episode 删除的 WebUI 适配层——把 Palimpsest 的多文件批处理接口收窄成
    「单个 owner + 单条 entry_id」的单点操作，供路由直接消费。
    """

    def __init__(self, memory_root: Path, *, everos_bin: str = "everos") -> None:
        self._layout = Layout(
            everos_root=memory_root,
            # episode 删除不碰插件印象缓存（那是 co-redaction 场景，不是本操作范围）
            plugin_relationships=None,
            app_id=_DISK_APP_ID,
            project_id=_DISK_PROJECT_ID,
        )
        # 生产真机验证坐实：reindex_incremental 内部调用的
        # `everos cascade sync` 与 everos_gateway.cascade_sync 是两套独立子进程
        # 调用（各自面向不同调用方，见模块头），everos_bin 配置不共享——main.py
        # 的 lifespan 把同一个 settings.everos_bin 分别传给两边。
        self._engine = Palimpsest(self._layout, everos_bin=everos_bin)

    def _selector_and_op(self, *, owner_id: str, entry_id: str) -> tuple[Selector, DeleteEntry]:
        sel = Selector(owner_ids=[owner_id], kinds=["episode"], include_plugin_caches=False)
        op = DeleteEntry(entry_ids=frozenset({entry_id}))
        return sel, op

    def preview_delete(self, *, owner_id: str, entry_id: str) -> EpisodeDeletePreview:
        """dry-run：定位目标 entry、算出会改哪个文件，零写入。"""
        sel, op = self._selector_and_op(owner_id=owner_id, entry_id=entry_id)
        plan: Plan = self._engine.plan(sel, op)
        return EpisodeDeletePreview(
            plan_id=plan.plan_id, is_empty=plan.is_empty(), render=plan.render()
        )

    def apply_delete(
        self, *, owner_id: str, entry_id: str, reindex_mode: ReindexMode = "incremental"
    ) -> EpisodeDeleteResult:
        """真正执行删除。调用方（路由层）须已确认 everos 已停机——本方法不做该
        检查，Palimpsest.apply 内部的 `_guard_stopped()` 是最后一道防线，但面向
        WebUI 用户的清楚错误信息应该在路由层用 gateway.is_everos_stopped() 提前给。
        """
        sel, op = self._selector_and_op(owner_id=owner_id, entry_id=entry_id)
        receipt = self._engine.apply(sel, op, yes=True, drop_index=(reindex_mode == "full"))
        return EpisodeDeleteResult(
            txn=receipt.get("txn", ""),
            status=receipt["status"],
            reindex_mode=reindex_mode,
            reindex_paths=receipt.get("reindex_paths", []),
        )

    def reindex_incremental(self, txn: str) -> dict:
        """重启 everos 后，对某次 incremental apply 强制同步索引。"""
        return self._engine.reindex_incremental(txn)
