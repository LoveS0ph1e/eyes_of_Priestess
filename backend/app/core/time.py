"""时间工具 —— 审计/签发等需 ISO8601 时间戳时统一经此。

集中于一处，便于将来换可控时钟做测试（与 auth.token.issue(now=) 同款『时间可注入』
风格：测试可不动系统时钟即冻结时间）。后端运行时取真实 UTC。
"""

from __future__ import annotations

from datetime import UTC, datetime


def now_iso() -> str:
    """当前 UTC 时间的 ISO8601 字符串。审计记录 at 字段经此注入。"""
    return datetime.now(UTC).isoformat()
