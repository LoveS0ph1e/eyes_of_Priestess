"""铭契（eternal_covenant）配置存储 —— 第一期核心，零 EverOS 耦合。

铭契是插件自己的配置（_conf_schema.json 的 eternal_covenant 字段），
形如 JSON 字符串 {"<QQ号>": "<固定核心设定文本>"}，完全不碰 EverOS。
改它零引擎耦合、零 flush 回退风险。

⚠️ 插件配置文件含 UTF-8 BOM：
  读用 encoding='utf-8-sig'（吞 BOM，有无 BOM 都安全），写用 encoding='utf-8'
  （不回写 BOM）。插件从不直接读盘——AstrBot 读盘再注入 self.config，
  AstrBot 的配置加载器对 BOM 容错（utf-8-sig 读两情况都能解），故写不回 BOM 安全。

双层数据结构（读插件 core/injection.resolve_covenant 坐实）：
  外层 JSON: {"everos_base_url": "...", "eternal_covenant": "<内层 JSON 字符串>", ...}
  内层是 {"<QQ号>": "<铭契文本>"} 的 dict，作为字符串塞在外层。
  读：json.loads(外层) → 取 eternal_covenant → json.loads(内层) → dict。
  写：构造 dict → json.dumps(dict, ensure_ascii=False) → 作为字符串写回外层同键。
  值内引号由 json.dumps 自动转义，勿手动转义。

身份三铁律：本模块不自行校验 user_id（留给 identity_resolver），调用方先过
  identity_resolver.resolve。这里只负责读/写磁盘契约。

原子写：temp + os.replace（同目录保证同文件系统 rename 原子）+ 写前拷贝 .bak。
  双写竞态防护：插件配置同时被 AstrBot 热读，os.replace 让 astrbot 永不读到半写 JSON。
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Covenant:
    """一条铭契。按 user_id 锁定的永久核心设定。"""

    user_id: str
    text: str


class CovenantStoreError(Exception):
    """铭契读写失败（文件缺失、JSON 非法、BOM 处理异常等）。"""


def _load_outer(path: Path) -> dict:
    """读外层 JSON（utf-8-sig 吞 BOM）。文件缺失/非法 → CovenantStoreError。"""
    try:
        raw = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as e:
        raise CovenantStoreError(f"插件配置文件不存在: {path}") from e
    except OSError as e:
        raise CovenantStoreError(f"读取插件配置失败: {path}: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise CovenantStoreError(f"插件配置 JSON 非法: {e}") from e
    if not isinstance(data, dict):
        raise CovenantStoreError("插件配置顶层不是 JSON 对象")
    return data


def _parse_table(value: object) -> dict[str, str]:
    """把 eternal_covenant 字段值解析成 {user_id: text} dict。

    - 缺失 / None / 空串 → {}（有效：无人有铭契）
    - 已是 dict（人为直存为对象）→ 原 dict
    - 非空 JSON 字符串 → json.loads
    - 解析失败 / 非对象 / 值非字符串 → CovenantStoreError（疑似损坏，勿静默吞）
    """
    if value is None or value == "":
        return {}
    if isinstance(value, dict):
        table = value
    elif isinstance(value, str):
        try:
            table = json.loads(value)
        except json.JSONDecodeError as e:
            raise CovenantStoreError(f"eternal_covenant 内层 JSON 非法: {e}") from e
    else:
        raise CovenantStoreError(
            f"eternal_covenant 类型异常: {type(value).__name__}（应为 JSON 字符串）"
        )
    if not isinstance(table, dict):
        raise CovenantStoreError("eternal_covenant 解析后不是 JSON 对象")
    # 键、值都必须是字符串；遇异形即报 CovenantStoreError（疑似人为损坏，勿静默吞）。
    cleaned: dict[str, str] = {}
    for k, v in table.items():
        if not isinstance(k, str):
            raise CovenantStoreError(f"eternal_covenant 键非字符串: {k!r}")
        if isinstance(v, str):
            cleaned[k] = v
        else:
            raise CovenantStoreError(f"eternal_covenant[{k!r}] 值非字符串: {type(v).__name__}")
    return cleaned


def _dump_outer(outer: dict, table: dict[str, str]) -> str:
    """把 table 序列化成内层 JSON 字符串，塞回外层同键，整体 dump。

    保留外层其它所有字段（仅替换 eternal_covenant 键）。dump 用 ensure_ascii=False
    保中文可读、indent=2。
    """
    outer = dict(outer)  # 浅拷贝，不改入参
    outer["eternal_covenant"] = json.dumps(table, ensure_ascii=False)
    return json.dumps(outer, ensure_ascii=False, indent=2)


class CovenantStore:
    """铭契配置读写。

    Args:
        config_path: 插件配置文件路径（生产为服务器上的 abconf JSON，带 BOM）。
                     开发期可指向本地样例。

    每次成功写（upsert/delete 实际落盘）后，``last_backup_path`` 指向写前备份的
    ``.bak`` 文件（供 audit_log 记录回滚位置）。未写或写未实际落盘时为 None。
    """

    def __init__(self, config_path: str) -> None:
        self.config_path = config_path
        self.last_backup_path: str | None = None

    def _path(self) -> Path:
        return Path(self.config_path)

    async def list_all(self) -> list[Covenant]:
        """列出全部铭契。按 user_id 排序稳定输出（QQ 号字典序）。"""
        outer = _load_outer(self._path())
        table = _parse_table(outer.get("eternal_covenant"))
        return [Covenant(user_id=uid, text=text) for uid, text in sorted(table.items())]

    async def get(self, user_id: str) -> Covenant | None:
        """取指定 user_id 的铭契。text 已 strip；无则 None。"""
        outer = _load_outer(self._path())
        table = _parse_table(outer.get("eternal_covenant"))
        text = table.get(user_id)
        if text is None:
            return None
        stripped = text.strip()
        if not stripped:
            return None
        return Covenant(user_id=user_id, text=stripped)

    async def upsert(self, user_id: str, text: str) -> None:
        """新增/覆盖一条铭契。空文本（strip 后空）= 删除该键（与 resolve_covenant
        的『空即无铭契』语义对齐）。原子写 + .bak。"""
        outer = _load_outer(self._path())
        table = _parse_table(outer.get("eternal_covenant"))
        stripped = text.strip()
        if stripped:
            table[user_id] = stripped
        else:
            table.pop(user_id, None)
        self._atomic_write(_dump_outer(outer, table))

    async def delete(self, user_id: str) -> bool:
        """删除一条铭契。存在则原子写回 + .bak，返 True；不存在返 False（不写盘）。"""
        outer = _load_outer(self._path())
        table = _parse_table(outer.get("eternal_covenant"))
        if user_id not in table:
            return False
        table.pop(user_id)
        self._atomic_write(_dump_outer(outer, table))
        return True

    def _atomic_write(self, content: str) -> None:
        """temp + os.replace 原子写，写前拷贝原文件到 .bak。

        tmp 与目标同目录 → os.replace 跨文件系统也能原子替换（POSIX rename 原子）。
        astrbot 热读时永不读到半写 JSON。fsync tmp 内容后 rename 增强崩溃耐久。
        """
        path = self._path()
        # 写前备份原文件（覆盖上次 .bak，滚动保留『上一版』可回滚）。
        if path.exists():
            bak = path.with_suffix(path.suffix + ".bak")
            shutil.copy2(path, bak)
            self.last_backup_path = str(bak)
        else:
            self.last_backup_path = None
        # 同目录临时文件，写完 fsync + os.replace。
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",  # 不回写 BOM（见模块头约束）
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        )
        try:
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        finally:
            tmp.close()
        os.replace(tmp.name, path)
