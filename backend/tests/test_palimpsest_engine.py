"""Palimpsest 引擎测试 —— 覆盖真机验证中发现并修复的两处缺口。

1. `_guard_stopped()` 裸调 `subprocess.run(["systemctl", ...])`，
   Windows/无 systemd 环境下抛 `FileNotFoundError` 不被捕获，会把裸 500
   捅穿到路由层（而不是走 `RuntimeError` → 路由层 409 的既定错误处理路径）。
   修复后须在 systemctl 不可用时抛 `RuntimeError`（与
   everos_gateway.HTTPEverOSGateway.is_everos_stopped() 对同一失败模式的
   处理一致），而不是让原始异常逃逸。

2. `reindex_incremental()` 硬编码裸命令 `"everos"`——生产真机验证（停机→delete→
   启机→reindex 全链路）坐实：`everos` 装在 venv 内
   （~/everos/.venv/bin/everos），systemd 服务的 PATH 里没有这个目录，裸命令
   会 subprocess FileNotFoundError。修复：`Palimpsest.__init__` 加
   `everos_bin` 参数（默认仍是裸名 "everos"，兼容已把 venv 加进 PATH 的环境）。

3. `reindex_incremental()` 把自己算好的相对路径字符串传给
   `everos cascade sync <path>`——但该 CLI 内部会对收到的 path 自己做一次
   `.expanduser().resolve()`（见 everos 源码 `_resolve_relative`），这一步是
   相对当前工作目录解析，不是相对 memory root；传一个"已经是相对路径"的字符
   串进去，resolve 出来的绝对路径跟真正的 memory root 完全不沾边，报
   "not under memory root"（生产真机验证坐实）。修复：改传绝对路径，让 CLI
   自己算相对路径，不越权代它做这一步。

全合成假数据（owner_id=9999999999），零真实隐私进仓。
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from app.modules.palimpsest import DeleteEntry, Layout, Palimpsest, Selector

OWNER = "9999999999"

EPISODE_MD = """---
entry_count: 1
---
<!-- entry:ep_20260101_00000001 -->
## ep_20260101_00000001

synthetic entry for palimpsest engine tests
<!-- /entry:ep_20260101_00000001 -->
"""


def _make_engine(tmp_path: Path) -> Palimpsest:
    ep_dir = tmp_path / "astrbot" / "default_project" / "users" / OWNER / "episodes"
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode-2026-01-01.md").write_text(EPISODE_MD, encoding="utf-8")
    layout = Layout(everos_root=tmp_path)
    return Palimpsest(layout)


def _sel_op() -> tuple[Selector, DeleteEntry]:
    sel = Selector(owner_ids=[OWNER], kinds=["episode"], include_plugin_caches=False)
    op = DeleteEntry(entry_ids=frozenset({"ep_20260101_00000001"}))
    return sel, op


async def test_guard_stopped_raises_runtime_error_when_systemctl_missing(tmp_path):
    """真机验证坐实的 bug：systemctl 不可用（FileNotFoundError）时必须转成
    RuntimeError，而不是让原始异常穿透——路由层只对 RuntimeError 做了 409 映射。
    """
    eng = _make_engine(tmp_path)
    with patch("app.modules.palimpsest.engine.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError, match="systemctl unavailable"):
            eng._guard_stopped()


async def test_guard_stopped_raises_runtime_error_when_active(tmp_path):
    eng = _make_engine(tmp_path)

    class _Active:
        stdout = "active\n"

    with patch("app.modules.palimpsest.engine.subprocess.run", return_value=_Active()):
        with pytest.raises(RuntimeError, match="active"):
            eng._guard_stopped()


async def test_guard_stopped_passes_when_inactive(tmp_path):
    eng = _make_engine(tmp_path)

    class _Inactive:
        stdout = "inactive\n"

    with patch("app.modules.palimpsest.engine.subprocess.run", return_value=_Inactive()):
        eng._guard_stopped()  # 不抛即通过


async def test_apply_surfaces_runtime_error_not_raw_exception(tmp_path):
    """apply() 在 _guard_stopped 失败时应让调用方（episode_editor/路由）拿到
    RuntimeError 可捕获，而不是裸 FileNotFoundError（真机验证时观察到的 500）。
    """
    eng = _make_engine(tmp_path)
    sel, op = _sel_op()
    with patch("app.modules.palimpsest.engine.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError):
            eng.apply(sel, op, yes=True)


async def test_apply_succeeds_when_stopped(tmp_path):
    """回归：修复没有破坏"已停机时正常 apply"这条主路径。"""
    eng = _make_engine(tmp_path)
    sel, op = _sel_op()

    class _Inactive:
        stdout = "inactive\n"

    with patch("app.modules.palimpsest.engine.subprocess.run", return_value=_Inactive()):
        receipt = eng.apply(sel, op, yes=True)
    assert receipt["status"] == "applied"
    md_path = (
        tmp_path
        / "astrbot"
        / "default_project"
        / "users"
        / OWNER
        / "episodes"
        / "episode-2026-01-01.md"
    )
    text = md_path.read_text(encoding="utf-8")
    assert "ep_20260101_00000001" not in text
    assert "entry_count: 0" in text


async def test_reindex_incremental_uses_configured_everos_bin(tmp_path):
    """生产真机验证坐实的 bug：reindex_incremental 必须用 __init__ 传入的
    everos_bin（全路径），而不是硬编码裸命令 "everos"（生产 systemd 服务的
    PATH 里没有这个目录，裸命令会 subprocess FileNotFoundError）。
    """
    ep_dir = tmp_path / "astrbot" / "default_project" / "users" / OWNER / "episodes"
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode-2026-01-01.md").write_text(EPISODE_MD, encoding="utf-8")
    layout = Layout(everos_root=tmp_path)
    eng = Palimpsest(layout, everos_bin="/home/youruser/everos/.venv/bin/everos")

    # 先跑一次 incremental apply，让 journal 里有一条 incremental_pending 记录。
    sel, op = _sel_op()

    class _Inactive:
        stdout = "inactive\n"

    with patch("app.modules.palimpsest.engine.subprocess.run", return_value=_Inactive()):
        receipt = eng.apply(sel, op, yes=True, drop_index=False)
    assert receipt["reindex"] == "incremental_pending"

    class _SyncOk:
        returncode = 0
        stdout = "sync complete\n"
        stderr = ""

    with patch("app.modules.palimpsest.engine.subprocess.run", return_value=_SyncOk()) as mock_run:
        eng.reindex_incremental(receipt["txn"])
    args = mock_run.call_args[0][0]
    assert args[0] == "/home/youruser/everos/.venv/bin/everos"
    assert args[1:3] == ["cascade", "sync"]
    # 生产真机验证坐实的 bug：必须传绝对路径，不能传我们自己算好的相对路径
    # （everos CLI 内部会对收到的 path 再做一次 relative-to-root 解析，
    # 传相对路径进去会被当成相对 cwd，resolve 出来的绝对路径完全跑偏）。
    assert Path(args[3]).is_absolute()
    assert args[3] == str(
        tmp_path
        / "astrbot"
        / "default_project"
        / "users"
        / OWNER
        / "episodes"
        / "episode-2026-01-01.md"
    )


async def test_reindex_incremental_defaults_to_bare_everos(tmp_path):
    """向后兼容：不传 everos_bin 时仍用裸命令 "everos"（PATH 里已有 venv 的环境）。"""
    ep_dir = tmp_path / "astrbot" / "default_project" / "users" / OWNER / "episodes"
    ep_dir.mkdir(parents=True)
    (ep_dir / "episode-2026-01-01.md").write_text(EPISODE_MD, encoding="utf-8")
    layout = Layout(everos_root=tmp_path)
    eng = Palimpsest(layout)  # 不传 everos_bin
    sel, op = _sel_op()

    class _Inactive:
        stdout = "inactive\n"

    with patch("app.modules.palimpsest.engine.subprocess.run", return_value=_Inactive()):
        receipt = eng.apply(sel, op, yes=True, drop_index=False)

    class _SyncOk:
        returncode = 0
        stdout = ""
        stderr = ""

    with patch("app.modules.palimpsest.engine.subprocess.run", return_value=_SyncOk()) as mock_run:
        eng.reindex_incremental(receipt["txn"])
    assert mock_run.call_args[0][0][0] == "everos"
