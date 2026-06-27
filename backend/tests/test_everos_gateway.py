"""HTTPEverOSGateway 解析测试 —— 据 真机契约，验信封/字段映射/错误处理。

fixture 全合成假数据（user_id=9999999999、假摘要），零真实隐私进仓：
结构镜像 真机返回（{request_id, data:{profiles/episodes/total_count/...}}），
只为坐实 HTTPEverOSGateway 的字段映射对不对——不是抓真机隐私存盘。

桩法：monkeypatch HTTPEverOSGateway._client 的 post/get 返假 response（省 respx 依赖）。
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.everos_gateway import (
    EverOSUnavailable,
    HTTPEverOSGateway,
)

# ── 全合成 fixture（镜像真机结构，零真实隐私）──────────────────────

PROFILE_DATA = {
    "request_id": "test-req-1",
    "data": {
        "episodes": [],
        "profiles": [
            {
                "id": "9999999999",
                "user_id": "9999999999",
                "app_id": "astrbot",
                "project_id": "default",
                "profile_data": {
                    "summary": "测试用画像摘要（合成）",
                    "explicit_info": [
                        {
                            "category": "测试类目",
                            "description": "测试显式描述",
                            "evidence": "测试证据",
                        }
                    ],
                    "implicit_traits": [
                        {
                            "trait": "测试特质",
                            "description": "测试隐式描述",
                            "evidence": "测试证据",
                            "basis": "测试依据",
                        }
                    ],
                },
                "profile_timestamp_ms": 1700000000000,
            }
        ],
        "agent_cases": [],
        "agent_skills": [],
        "total_count": 1,
        "count": 1,
    },
}

EPISODE_DATA = {
    "request_id": "test-req-2",
    "data": {
        "episodes": [
            {
                "id": "9999999999_ep_20260101_00000001",
                "user_id": "9999999999",
                "app_id": "astrbot",
                "project_id": "default",
                "session_id": "Test:FriendMessage:9999999999",
                "timestamp": "2026-01-01T00:00:00Z",
                "sender_ids": ["9999999999", "1000000002"],
                "summary": "测试 episode 摘要（合成）",
                "subject": "测试 episode 标题",
                "episode": "测试 episode 全文（合成）",
                "type": "Conversation",
            }
        ],
        "profiles": [],
        "agent_cases": [],
        "agent_skills": [],
        "total_count": 155,
        "count": 1,
    },
}

SEARCH_DATA = {
    "request_id": "test-req-3",
    "data": {
        "episodes": [
            {
                "id": "9999999999_ep_20260101_00000002",
                "user_id": "9999999999",
                "summary": "命中 episode（合成）",
                "subject": "命中标题",
                "episode": "命中全文",
                "timestamp": "2026-01-01T01:00:00Z",
                "score": 0.42,
                "atomic_facts": [{"id": "af1", "content": "原子事实", "score": 0.42}],
            }
        ],
        "profiles": [],
        "unprocessed_messages": [],
    },
}


class _FakeResp:
    """假 httpx response：可控 json + raise_for_status。"""

    def __init__(self, payload, *, status_ok=True):
        self._payload = payload
        self._ok = status_ok

    def json(self):
        return self._payload

    def raise_for_status(self):
        if not self._ok:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


def _gateway_with(monkeypatch, *, post_payload=None, get_payload=None, post_ok=True, get_ok=True):
    """造一个 HTTPEverOSGateway 并桩其 _client.post/get。"""
    gw = HTTPEverOSGateway("http://127.0.0.1:8596", timeout=5.0)

    async def fake_post(url, json=None):
        return _FakeResp(post_payload, status_ok=post_ok)

    async def fake_get(url):
        return _FakeResp(get_payload, status_ok=get_ok)

    monkeypatch.setattr(gw._client, "post", fake_post)
    monkeypatch.setattr(gw._client, "get", fake_get)
    return gw


# ── profile ───────────────────────────────────────────────────────


async def test_get_profile_maps_profile_data(monkeypatch):
    """三字段从 profile_data 子对象取；explicit/implicit 保结构化对象。"""
    gw = _gateway_with(monkeypatch, post_payload=PROFILE_DATA)
    p = await gw.get_profile(user_id="9999999999", app_id="astrbot", project_id="default")
    assert p is not None
    assert p.user_id == "9999999999"
    assert p.summary == "测试用画像摘要（合成）"
    assert p.explicit[0]["category"] == "测试类目"
    assert p.explicit[0]["description"] == "测试显式描述"
    assert p.implicit[0]["trait"] == "测试特质"
    assert p.implicit[0]["basis"] == "测试依据"
    assert p.raw["profile_timestamp_ms"] == 1700000000000  # 全量进 raw


async def test_get_profile_empty_returns_none(monkeypatch):
    empty = {"request_id": "x", "data": {"profiles": [], "total_count": 0}}
    gw = _gateway_with(monkeypatch, post_payload=empty)
    assert (
        await gw.get_profile(user_id="9999999999", app_id="astrbot", project_id="default") is None
    )


# ── episode ───────────────────────────────────────────────────────


async def test_list_episodes_maps_and_total(monkeypatch):
    """entry_id=真机 id；subject 映射；total_count 透传（不是 len）。"""
    gw = _gateway_with(monkeypatch, post_payload=EPISODE_DATA)
    eps, total = await gw.list_episodes(
        user_id="9999999999", app_id="astrbot", project_id="default", page=1, page_size=3
    )
    assert total == 155  # 真实总数透传，非当页条数
    assert len(eps) == 1
    assert eps[0].entry_id == "9999999999_ep_20260101_00000001"
    assert eps[0].subject == "测试 episode 标题"
    assert eps[0].summary == "测试 episode 摘要（合成）"
    assert eps[0].raw["episode"] == "测试 episode 全文（合成）"  # 全文在 raw


# ── search ────────────────────────────────────────────────────────


async def test_search_returns_raw_data(monkeypatch):
    """search 返 data 原始结构（含 score/atomic_facts），前端要 episodes/profiles。"""
    gw = _gateway_with(monkeypatch, post_payload=SEARCH_DATA)
    data = await gw.search(
        query="测试", user_id="9999999999", app_id="astrbot", project_id="default"
    )
    assert len(data["episodes"]) == 1
    assert data["episodes"][0]["score"] == 0.42
    assert data["episodes"][0]["atomic_facts"][0]["content"] == "原子事实"
    assert data["profiles"] == []


# ── health ────────────────────────────────────────────────────────


async def test_health_ok(monkeypatch):
    gw = _gateway_with(monkeypatch, get_payload={"status": "ok"})
    assert await gw.health() is True


async def test_health_nested_status(monkeypatch):
    """兼容包络 {data:{status:...}}。"""
    gw = _gateway_with(monkeypatch, get_payload={"data": {"status": "healthy"}})
    assert await gw.health() is True


async def test_health_bad_status_false(monkeypatch):
    gw = _gateway_with(monkeypatch, get_payload={"status": "degraded"})
    assert await gw.health() is False


async def test_health_http_error_false(monkeypatch):
    gw = _gateway_with(monkeypatch, get_payload={"status": "ok"}, get_ok=False)
    assert await gw.health() is False


# ── 错误包络 ──────────────────────────────────────────────────────


async def test_post_http_error_raises_unavailable(monkeypatch):
    gw = _gateway_with(monkeypatch, post_payload=PROFILE_DATA, post_ok=False)
    with pytest.raises(EverOSUnavailable):
        await gw.get_profile(user_id="9999999999", app_id="astrbot", project_id="default")


async def test_post_error_envelope_raises_unavailable(monkeypatch):
    """body 含 error 键 → EverOSUnavailable（即便 HTTP 200）。"""
    err_body = {"request_id": "x", "error": {"message": "user not found"}}
    gw = _gateway_with(monkeypatch, post_payload=err_body)
    with pytest.raises(EverOSUnavailable):
        await gw.get_profile(user_id="9999999999", app_id="astrbot", project_id="default")
