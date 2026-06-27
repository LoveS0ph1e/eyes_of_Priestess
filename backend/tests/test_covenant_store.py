"""covenant_store 真实现测试 —— 第一期核心模块的磁盘契约零整。

覆盖：
  - 双层 JSON-within-JSON（外层字符串、内层 dict）结构正确
  - 读带/不带 UTF-8 BOM 都能解析（utf-8-sig 兼容两情况）
  - upsert 后读回一致；值内引号/中文/换行不丢不转义错
  - 空文本 upsert = 删除该键
  - delete 后该键消失；不存在返 False 不写盘
  - .bak 写前生成；其它字段不被破坏
  - 损坏 JSON / 文件缺失 → CovenantStoreError
  - 路由层：未登录 PUT/DELETE → 401/503；带 token upsert → 200 + audit 落地
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.modules.auth import SESSION_COOKIE
from app.modules.covenant_store import (
    Covenant,
    CovenantStore,
    CovenantStoreError,
)

SECRET = "test-secret-token-very-long-for-hmac-1234567890"
QQ_A = "1000000001"
QQ_B = "1000000002"


def _write_config(path: Path, *, bom: bool, table: dict | None = None) -> None:
    """造一个符合契约的外层 JSON：含其它字段 + eternal_covenant（内层 JSON 字符串）。"""
    outer = {
        "everos_base_url": "http://everos:8000",  # 其它字段，要测不被破坏
        "project_id": "default",
        "eternal_covenant": json.dumps(table or {}, ensure_ascii=False),
    }
    data = json.dumps(outer, ensure_ascii=False, indent=2)
    prefix = "﻿" if bom else ""
    path.write_text(prefix + data, encoding="utf-8")


def _read_table(path: Path) -> dict:
    """从磁盘用插件同款方式读回内层 table（坐实双层结构）。"""
    outer = json.loads(path.read_text(encoding="utf-8-sig"))
    return json.loads(outer["eternal_covenant"])


@pytest.fixture
def config_path(tmp_path):
    """插件配置文件路径（不带 BOM 的样例）。"""
    p = tmp_path / "config.json"
    _write_config(p, bom=False, table={QQ_A: "测试用户的旧铭契"})
    return p


@pytest.fixture
def bom_config_path(tmp_path):
    """带 UTF-8 BOM 的样例（坐实 dest 文档的 BOM 断言）。"""
    p = tmp_path / "config.bom.json"
    _write_config(p, bom=True, table={QQ_A: "BOM 用户的铭契"})
    return p


# ── 读 ────────────────────────────────────────────────────────────────────


async def test_list_all_returns_sorted(config_path):
    store = CovenantStore(str(config_path))
    _write_config(config_path, bom=False, table={QQ_B: "B文本", QQ_A: "A文本"})
    rows = await store.list_all()
    assert rows == [Covenant(QQ_A, "A文本"), Covenant(QQ_B, "B文本")]


async def test_get_returns_stripped_text(config_path):
    store = CovenantStore(str(config_path))
    _write_config(config_path, bom=False, table={QQ_A: "  带空白的铭契  "})
    assert (await store.get(QQ_A)) == Covenant(QQ_A, "带空白的铭契")


async def test_get_missing_returns_none(config_path):
    store = CovenantStore(str(config_path))
    assert await store.get("9999999999") is None


async def test_get_empty_text_returns_none(config_path):
    """空字符串值 = 无铭契（与 resolve_covenant 的 strip+空即无语义对齐）。"""
    store = CovenantStore(str(config_path))
    _write_config(config_path, bom=False, table={QQ_A: "   "})
    assert await store.get(QQ_A) is None


async def test_read_handles_bom(bom_config_path):
    """带 UTF-8 BOM 的文件也能读取（utf-8-sig 兼容两情况）。"""
    store = CovenantStore(str(bom_config_path))
    assert (await store.get(QQ_A)) == Covenant(QQ_A, "BOM 用户的铭契")


async def test_missing_file_raises(config_path):
    store = CovenantStore(str(config_path.with_name("nope.json")))
    with pytest.raises(CovenantStoreError):
        await store.list_all()


async def test_corrupt_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not valid json", encoding="utf-8-sig")
    store = CovenantStore(str(p))
    with pytest.raises(CovenantStoreError):
        await store.list_all()


# ── 写 ────────────────────────────────────────────────────────────────────


async def test_upsert_then_read_back(config_path):
    store = CovenantStore(str(config_path))
    await store.upsert(QQ_B, "新增的完整铭契，含中文。")
    assert (await store.get(QQ_B)) == Covenant(QQ_B, "新增的完整铭契，含中文。")
    # 覆盖已有
    await store.upsert(QQ_A, "改写后的铭契")
    assert (await store.get(QQ_A)) == Covenant(QQ_A, "改写后的铭契")


async def test_upsert_preserves_double_layer_structure(config_path):
    """写回后磁盘仍是双层：外层字符串、内层 dict（用插件同款方式读回可解）。"""
    store = CovenantStore(str(config_path))
    await store.upsert(QQ_A, "校验双层结构")
    table = _read_table(config_path)  # 模拟插件读
    assert table == {QQ_A: "校验双层结构"}


async def test_upsert_preserves_other_fields(config_path):
    """写 eternal_covenant 不能破坏外层其它字段。"""
    store = CovenantStore(str(config_path))
    await store.upsert(QQ_B, "新文本")
    outer = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert outer["everos_base_url"] == "http://everos:8000"
    assert outer["project_id"] == "default"


async def test_upsert_value_with_quotes_and_newlines(config_path):
    """值内引号/换行/中文不丢不转义错（json.dumps 自动处理，勿手动转义）。"""
    store = CovenantStore(str(config_path))
    tricky = '含"引号"和\n换行，及『中文符号』'
    await store.upsert(QQ_A, tricky)
    assert (await store.get(QQ_A)).text == tricky
    # 插件从磁盘读回也一致
    assert _read_table(config_path)[QQ_A] == tricky


async def test_upsert_empty_text_deletes_key(config_path):
    """空文本 upsert = 删除该键（与 get 的空即无语义对齐）。"""
    store = CovenantStore(str(config_path))
    assert (await store.get(QQ_A)) is not None
    await store.upsert(QQ_A, "   ")
    assert await store.get(QQ_A) is None
    assert QQ_A not in _read_table(config_path)


async def test_delete_existing_members(config_path):
    store = CovenantStore(str(config_path))
    removed = await store.delete(QQ_A)
    assert removed is True
    assert await store.get(QQ_A) is None


async def test_delete_missing_returns_false_no_write(config_path):
    """不存在 → False 且不写盘（无 .bak、mtime 不变）。"""
    before_mtime = config_path.stat().st_mtime
    store = CovenantStore(str(config_path))
    removed = await store.delete("9999999999")
    assert removed is False
    assert store.last_backup_path is None
    assert not (config_path.with_suffix(".json.bak")).exists()
    assert config_path.stat().st_mtime == before_mtime


async def test_write_creates_bak(config_path):
    """实际写盘前生成 .bak（上一版可回滚）。"""
    store = CovenantStore(str(config_path))
    original = config_path.read_text(encoding="utf-8-sig")
    await store.upsert(QQ_A, "新版")
    bak = config_path.with_suffix(".json.bak")
    assert bak.exists()
    assert bak.read_text(encoding="utf-8-sig") == original
    assert store.last_backup_path is not None


async def test_write_does_not_write_bom(config_path):
    """写回用 utf-8，不回写 BOM（前三字节不是 efbbbf）。"""
    store = CovenantStore(str(config_path))
    await store.upsert(QQ_A, "无 BOM")
    assert config_path.read_bytes()[:3] != b"\xef\xbb\xbf"
    # 但 utf-8-sig 仍能读回（BOM 有无皆可）
    outer = json.loads(config_path.read_text(encoding="utf-8-sig"))
    assert json.loads(outer["eternal_covenant"])[QQ_A] == "无 BOM"


async def test_write_preserves_bom_file(bom_config_path):
    """读带 BOM 文件后写回：内容正确、不回写 BOM、双层结构保留。"""
    store = CovenantStore(str(bom_config_path))
    await store.upsert(QQ_B, "BOM 文件新增")
    # 不回写 BOM
    assert bom_config_path.read_bytes()[:3] != b"\xef\xbb\xbf"
    table = _read_table(bom_config_path)
    assert table[QQ_A] == "BOM 用户的铭契"  # 原有保留
    assert table[QQ_B] == "BOM 文件新增"


# ── 路由层（鉴权门 + audit 落地）────────────────────────────────────────


@pytest.fixture
def app_client(monkeypatch, tmp_path):
    """配密钥 + config/audit 指向 tmp，避免误写生产 data。"""
    from app.core import config as config_module
    from app.main import app

    cfg = tmp_path / "config.json"
    _write_config(cfg, bom=False, table={QQ_A: "路由旧铭契"})
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(
        config_module,
        "settings",
        Settings(
            host="127.0.0.1",
            auth_secret=SECRET,
            plugin_config_path=str(cfg),
            audit_log_path=str(audit),
        ),
    )
    # with 触发 lifespan，使单例按上面的 settings 建进 app.state（指向 tmp）。
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, cfg, audit


def _login_token(client):
    resp = client.post("/api/auth/login", json={"secret": SECRET})
    assert resp.status_code == 200, resp.text
    return resp.json()["token"]


def test_route_list_returns_existing(app_client):
    client, cfg, _ = app_client
    resp = client.get("/api/covenant")
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert any(r["user_id"] == QQ_A and r["text"] == "路由旧铭契" for r in rows)


def test_route_upsert_requires_token(app_client):
    client, _, _ = app_client
    resp = client.put(f"/api/covenant/{QQ_B}", json={"user_id": QQ_B, "text": "x"})
    assert resp.status_code == 401


async def test_route_upsert_with_token_writes_and_audits(app_client):
    client, cfg, audit = app_client
    token = _login_token(client)
    resp = client.put(
        f"/api/covenant/{QQ_B}",
        json={"user_id": QQ_B, "text": "路由新增铭契"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    # 写入生效（用 store 直读磁盘坐实）
    store = CovenantStore(str(cfg))
    assert (await store.get(QQ_B)).text == "路由新增铭契"
    # audit 落地
    log_lines = audit.read_text(encoding="utf-8").splitlines()
    assert len(log_lines) == 1
    entry = json.loads(log_lines[0])
    assert entry["action"] == "covenant.upsert"
    assert entry["user_id"] == QQ_B
    assert entry["detail"]["before"] is None
    assert entry["detail"]["after"] == "路由新增铭契"
    assert entry["backup_path"] is not None  # 有 .bak


def test_route_delete_audits(app_client):
    client, cfg, audit = app_client
    token = _login_token(client)
    resp = client.delete(f"/api/covenant/{QQ_A}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["removed"] is True
    # audit
    entry = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
    assert entry["action"] == "covenant.delete"
    assert entry["user_id"] == QQ_A
    assert entry["detail"]["before"] == "路由旧铭契"
    assert entry["detail"]["removed"] is True


def test_route_upsert_with_token_via_cookie(app_client):
    """token 双来源之一（cookie）也应能过鉴权门。"""
    client, _, _ = app_client
    token = _login_token(client)
    client.cookies.set(SESSION_COOKIE, token, domain="testserver")
    resp = client.put(
        f"/api/covenant/{QQ_B}",
        json={"user_id": QQ_B, "text": "cookie 写入"},
    )
    assert resp.status_code == 200, resp.text


def test_route_unsafe_user_id_rejected(app_client):
    """身份三铁律：body user_id 为 'default'（铁律2禁回退）→ 400，绝不落盘。

    用路由参数传一个合法占位段、body 传非法 user_id，验证 resolve_identity 在落盘前拦截。
    """
    client, _, _ = app_client
    token = _login_token(client)
    resp = client.put(
        f"/api/covenant/{QQ_B}",
        json={"user_id": "default", "text": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 400


@pytest.fixture
def readonly_client(monkeypatch, tmp_path):
    """只读模式（WEBUI_COVENANT_READONLY=1）下的客户端 + tmp 配置/审计。"""
    from app.core import config as config_module
    from app.main import app

    cfg = tmp_path / "config.json"
    _write_config(cfg, bom=False, table={QQ_A: "只读模式原有铭契"})
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setattr(
        config_module,
        "settings",
        Settings(
            host="127.0.0.1",
            auth_secret=SECRET,
            plugin_config_path=str(cfg),
            audit_log_path=str(audit),
            covenant_readonly=True,
        ),
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c, cfg, audit


async def test_route_readonly_blocks_upsert(readonly_client):
    """只读开关打开时 upsert → 502 且不落盘（防误改生产）。"""
    client, cfg, _ = readonly_client
    token = _login_token(client)
    before_mtime = cfg.stat().st_mtime
    resp = client.put(
        f"/api/covenant/{QQ_B}",
        json={"user_id": QQ_B, "text": "只读模式禁止写入"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 502
    # 不落盘：mtime 不变、磁盘仍是原内容
    assert cfg.stat().st_mtime == before_mtime
    store = CovenantStore(str(cfg))
    assert (await store.get(QQ_B)) is None


async def test_route_readonly_blocks_delete(readonly_client):
    """只读开关打开时 delete → 502 且不落盘。"""
    client, cfg, _ = readonly_client
    token = _login_token(client)
    before_mtime = cfg.stat().st_mtime
    resp = client.delete(f"/api/covenant/{QQ_A}", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 502
    assert cfg.stat().st_mtime == before_mtime
    store = CovenantStore(str(cfg))
    # 原铭契仍存在，未被删
    assert (await store.get(QQ_A)).text == "只读模式原有铭契"
