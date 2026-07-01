"""Palimpsest —— 停机原子记忆重写引擎（二期批处理后端，docs/06 §2）。

拷自 `F:\\Amadeus\\Palimpsest\\palimpsest.py`（原独立 CLI 工具，见该仓
`PALIMPSEST.md` 完整设计）。实现原样保留在 `engine.py`——只改了 CLI 入口
说明（`python -m app.modules.palimpsest ...` 取代独立脚本调用），逻辑
零改动。完整设计文档见同目录 `PALIMPSEST.md`。

本模块与 `everos_gateway` 的关系（不是二选一，是两个不同粒度的工具）：
  - `everos_gateway.read/write_user_markdown`：单文件、乐观锁、面向 WebUI
    路由的即时读写（改一条 episode）。
  - `Palimpsest`：Selector 扫描多文件的批处理引擎，面向"外科去错误归属"
    "整条删除并维护 entry_count"这类需要快照+回滚+跨派生件一致性的操作。
episode_editor 等二期路由按需选用其一，不是本模块吃掉 everos_gateway 的活。

安全边界（继承自 PALIMPSEST.md INV-2/INV-5/INV-6，二期未变）：
  apply/rollback 须 everos 已停机（自检，非 WebUI 代 stop/start）；先快照
  后写、失败自动回滚；journal 不落 PII 明文（只存 ref + 哈希前缀）。
"""

from __future__ import annotations

from .engine import (
    KIND_GLOBS,
    DeleteEntry,
    FileChange,
    Layout,
    Operation,
    Palimpsest,
    Plan,
    RedactSpan,
    Selector,
)

__all__ = [
    "DeleteEntry",
    "FileChange",
    "KIND_GLOBS",
    "Layout",
    "Operation",
    "Palimpsest",
    "Plan",
    "RedactSpan",
    "Selector",
]
