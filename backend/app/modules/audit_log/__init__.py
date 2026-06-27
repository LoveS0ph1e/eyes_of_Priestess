"""审计日志 —— 记录每次写操作（谁、何时、改了什么、备份在哪）。

记忆是敏感资产，写操作必须可追溯、可回滚。所有写接口（covenant upsert/delete、
episode 增删改、画像编辑）落一条 jsonl 记录。

第一期为最小够用：JSONL 追加写 + 尾部读最近 N 条。数据量小，不滚动不分片。
时间戳由调用方经 app.core.time.now_iso() 注入（与 auth.token.issue(now=) 同款，便于
将来换可控时钟做测试）——本模块不取系统时间，保持可测性一致。
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

_logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEntry:
    """一条审计记录。

    at 由调用方经 app.core.time.now_iso() 注入（与 token now= 同款可注入风格，
    便于将来换可控时钟做测试）。
    """

    at: str  # ISO8601
    actor: str  # 操作主体（auth principal.subject）
    action: str  # 'covenant.upsert' / 'episode.delete' / 'profile.snapshot' 等
    user_id: str  # 被操作的用户
    detail: dict[str, Any]  # 改了什么（before/after 摘要）
    backup_path: str | None  # 备份文件位置（可回滚）


class AuditLog:
    """追加式 jsonl 审计日志。"""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    async def record(self, entry: AuditEntry) -> None:
        """追加一条审计记录（jsonl，每行一条 json.dumps，ensure_ascii=False 保中文可读）。"""
        line = json.dumps(asdict(entry), ensure_ascii=False)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="") as f:
            f.write(line + "\n")

    async def list_recent(self, *, limit: int = 50) -> list[AuditEntry]:
        """读最近 N 条（按写入顺序倒序，最新的在前）。

        第一期数据量小：全量读取后取尾部 N 条反序。
        退场阈值：审计文件 > 5MB 时改『从尾部 seek 倒读 N 行』，避免全量 OOM（服务器
        available 仅 ~664M）。当前实现简单全量读，仅小数据量期安全。

        损坏/空行容错：损坏行跳过但告警——审计为安全资产，静默吞篡改/截断有追溯风险，
        故遇 JSONDecodeError/缺字段记录 warning 而非静默。
        """
        if not self.path.exists():
            return []
        entries: list[AuditEntry] = []
        with self.path.open("r", encoding="utf-8") as f:
            for lineno, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    # 容错但不静默：损坏行告警，便于运维察觉篡改/截断。
                    _logger.warning("audit_log 损坏行已跳过 %s:%d", self.path, lineno)
                    continue
                try:
                    entries.append(
                        AuditEntry(
                            at=obj["at"],
                            actor=obj["actor"],
                            action=obj["action"],
                            user_id=obj["user_id"],
                            detail=obj["detail"],
                            backup_path=obj.get("backup_path"),
                        )
                    )
                except KeyError as e:
                    _logger.warning("audit_log 行缺字段 %s %s:%d", e, self.path, lineno)
                    continue
        return list(reversed(entries[-limit:]))
