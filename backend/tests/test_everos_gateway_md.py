"""md 读写 / cascade_sync / is_everos_stopped 测试 —— 二期 ABC 扩写（docs/06 §5）。

覆盖两套实现：
  - MockEverOSGateway：进程内 dict 模拟磁盘，验证乐观锁语义本身。
  - HTTPEverOSGateway：tmp_path 当 memory_root，验证真实文件 IO + 路径解析
    + memory_root 未配置时的 EverOSGatewayMisconfigured 守卫。

全合成假数据（user_id=9999999999），零真实隐私进仓。
"""

from __future__ import annotations

import datetime as _dt
import hashlib
from unittest.mock import patch

import pytest

from app.modules.everos_gateway import (
    EverOSGatewayMisconfigured,
    HTTPEverOSGateway,
    MarkdownVersionConflict,
    MockEverOSGateway,
)

UID = "9999999999"
DATE = _dt.date(2026, 1, 1)


# ── MockEverOSGateway：乐观锁语义 ────────────────────────────────────


async def test_mock_read_missing_returns_none():
    gw = MockEverOSGateway()
    assert (
        await gw.read_user_markdown(
            user_id=UID, kind="episode", app_id="astrbot", project_id="default", date=DATE
        )
        is None
    )


async def test_mock_write_new_file_expects_empty_sha():
    """新建文件时 expected_sha256 须传空文本的哈希（约定见 ABC 文档字符串）。"""
    gw = MockEverOSGateway()
    empty_sha = hashlib.sha256(b"").hexdigest()
    doc = await gw.write_user_markdown(
        user_id=UID,
        kind="profile",
        app_id="astrbot",
        project_id="default",
        new_text="# 测试画像",
        expected_sha256=empty_sha,
    )
    assert doc.text == "# 测试画像"
    read_back = await gw.read_user_markdown(
        user_id=UID, kind="profile", app_id="astrbot", project_id="default"
    )
    assert read_back is not None
    assert read_back.text == "# 测试画像"
    assert read_back.sha256 == doc.sha256


async def test_mock_write_conflict_on_stale_sha():
    """expected_sha256 与现态不符 → MarkdownVersionConflict（模拟并发覆盖）。"""
    gw = MockEverOSGateway()
    empty_sha = hashlib.sha256(b"").hexdigest()
    await gw.write_user_markdown(
        user_id=UID,
        kind="profile",
        app_id="astrbot",
        project_id="default",
        new_text="第一版",
        expected_sha256=empty_sha,
    )
    with pytest.raises(MarkdownVersionConflict):
        await gw.write_user_markdown(
            user_id=UID,
            kind="profile",
            app_id="astrbot",
            project_id="default",
            new_text="踩过期版本写入",
            expected_sha256=empty_sha,  # 过期：磁盘已是"第一版"的哈希
        )


async def test_mock_daily_kind_requires_date():
    gw = MockEverOSGateway()
    with pytest.raises(ValueError, match="date"):
        await gw.read_user_markdown(
            user_id=UID, kind="episode", app_id="astrbot", project_id="default", date=None
        )


async def test_mock_unknown_kind_rejected():
    gw = MockEverOSGateway()
    with pytest.raises(ValueError, match="unknown kind"):
        await gw.read_user_markdown(
            user_id=UID, kind="not_a_kind", app_id="astrbot", project_id="default"
        )


async def test_mock_cascade_sync_ok():
    gw = MockEverOSGateway()
    result = await gw.cascade_sync("mock://whatever")
    assert result.ok is True


async def test_mock_is_everos_stopped_true():
    """mock 场景恒当作已停机，供上层停机专属流程在开发机跑通。"""
    gw = MockEverOSGateway()
    assert await gw.is_everos_stopped() is True


# ── HTTPEverOSGateway：真实文件 IO + memory_root 守卫 ────────────────


def _http_gw(memory_root=None):
    return HTTPEverOSGateway("http://127.0.0.1:8596", timeout=5.0, memory_root=memory_root)


async def test_http_misconfigured_without_memory_root():
    gw = _http_gw(memory_root=None)
    with pytest.raises(EverOSGatewayMisconfigured):
        await gw.read_user_markdown(
            user_id=UID, kind="profile", app_id="astrbot", project_id="default"
        )
    with pytest.raises(EverOSGatewayMisconfigured):
        await gw.cascade_sync("/some/path")


async def test_http_read_missing_returns_none(tmp_path):
    gw = _http_gw(memory_root=tmp_path)
    doc = await gw.read_user_markdown(
        user_id=UID, kind="profile", app_id="astrbot", project_id="default"
    )
    assert doc is None


async def test_http_write_resolves_default_app_project_dir(tmp_path):
    """app_id=astrbot/project_id=default → 磁盘 astrbot/default_project/（读码坐实的映射）。"""
    gw = _http_gw(memory_root=tmp_path)
    empty_sha = hashlib.sha256(b"").hexdigest()
    doc = await gw.write_user_markdown(
        user_id=UID,
        kind="profile",
        app_id="astrbot",
        project_id="default",
        new_text="# 画像正文",
        expected_sha256=empty_sha,
    )
    expected_path = tmp_path / "astrbot" / "default_project" / "users" / UID / "user.md"
    assert expected_path.is_file()
    assert expected_path.read_text(encoding="utf-8") == "# 画像正文"
    assert doc.path == str(expected_path)


async def test_http_write_daily_kind_path(tmp_path):
    """episode 是按日分片：目录 episodes/、文件名 episode-<date>.md。"""
    gw = _http_gw(memory_root=tmp_path)
    empty_sha = hashlib.sha256(b"").hexdigest()
    await gw.write_user_markdown(
        user_id=UID,
        kind="episode",
        app_id="astrbot",
        project_id="default",
        new_text="<!-- entry:ep_20260101_00000001 -->\nx\n<!-- /entry:ep_20260101_00000001 -->\n",
        expected_sha256=empty_sha,
        date=DATE,
    )
    expected_path = (
        tmp_path
        / "astrbot"
        / "default_project"
        / "users"
        / UID
        / "episodes"
        / "episode-2026-01-01.md"
    )
    assert expected_path.is_file()


async def test_http_write_conflict_on_stale_sha(tmp_path):
    gw = _http_gw(memory_root=tmp_path)
    empty_sha = hashlib.sha256(b"").hexdigest()
    await gw.write_user_markdown(
        user_id=UID,
        kind="profile",
        app_id="astrbot",
        project_id="default",
        new_text="第一版",
        expected_sha256=empty_sha,
    )
    with pytest.raises(MarkdownVersionConflict):
        await gw.write_user_markdown(
            user_id=UID,
            kind="profile",
            app_id="astrbot",
            project_id="default",
            new_text="踩过期版本写入",
            expected_sha256=empty_sha,
        )


async def test_http_atomic_fact_and_foresight_use_dot_prefixed_dirs(tmp_path):
    """atomic_fact/foresight 目录带点前缀（对齐 everos 包 DIR_NAME 约定）。"""
    gw = _http_gw(memory_root=tmp_path)
    empty_sha = hashlib.sha256(b"").hexdigest()
    await gw.write_user_markdown(
        user_id=UID,
        kind="atomic_fact",
        app_id="astrbot",
        project_id="default",
        new_text="af",
        expected_sha256=empty_sha,
        date=DATE,
    )
    await gw.write_user_markdown(
        user_id=UID,
        kind="foresight",
        app_id="astrbot",
        project_id="default",
        new_text="fs",
        expected_sha256=empty_sha,
        date=DATE,
    )
    users_dir = tmp_path / "astrbot" / "default_project" / "users" / UID
    assert (users_dir / ".atomic_facts" / "atomic_fact-2026-01-01.md").is_file()
    assert (users_dir / ".foresights" / "foresight-2026-01-01.md").is_file()


async def test_http_cascade_sync_invokes_everos_cli(tmp_path):
    """cascade_sync 拼相对路径调子进程；子进程本身桩掉（不依赖真机 everos CLI）。"""

    class _FakeCompleted:
        returncode = 0
        stdout = "sync complete\n"
        stderr = ""

    gw = _http_gw(memory_root=tmp_path)
    target = tmp_path / "astrbot" / "default_project" / "users" / UID / "user.md"
    target.parent.mkdir(parents=True)
    target.write_text("x", encoding="utf-8")

    with patch("app.modules.everos_gateway.subprocess.run", return_value=_FakeCompleted()) as m:
        result = await gw.cascade_sync(str(target))
    assert result.ok is True
    assert result.stdout == "sync complete"
    args = m.call_args[0][0]
    assert args[:3] == ["everos", "cascade", "sync"]
    assert args[3] == f"astrbot/default_project/users/{UID}/user.md"


async def test_http_is_everos_stopped_reflects_systemctl_output(tmp_path):
    gw = _http_gw(memory_root=tmp_path)

    class _Active:
        stdout = "active\n"

    class _Inactive:
        stdout = "inactive\n"

    with patch("app.modules.everos_gateway.subprocess.run", return_value=_Active()):
        assert await gw.is_everos_stopped() is False
    with patch("app.modules.everos_gateway.subprocess.run", return_value=_Inactive()):
        assert await gw.is_everos_stopped() is True


async def test_http_is_everos_stopped_conservative_when_systemctl_missing(tmp_path):
    """非 systemd 环境（如本机开发/Windows）：查不到 → 保守返回 False，不误判为已停机。"""
    gw = _http_gw(memory_root=tmp_path)
    with patch("app.modules.everos_gateway.subprocess.run", side_effect=FileNotFoundError()):
        assert await gw.is_everos_stopped() is False
