"""只读端点响应信封测试 —— 锁定 {data, meta} 统一约定（审计 #6）。

Mock 网关下三端点都应返回顶层 {data, meta}：
  - profile：data=画像或 null，meta=null
  - episodes：data=列表，meta={total, page, page_size}
  - search：data=EverOS 原始结构，meta=null
"""

from __future__ import annotations

QQ = "1000000001"


def test_profile_envelope_shape(client):
    resp = client.get(f"/api/view/profile/{QQ}")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data", "meta"}
    assert body["data"] is None  # mock 返空画像
    assert body["meta"] is None


def test_episodes_envelope_shape(client):
    resp = client.get(f"/api/view/episodes/{QQ}?page=2&page_size=5")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data", "meta"}
    assert body["data"] == []  # mock 返空列表
    assert body["meta"] == {"total": 0, "page": 2, "page_size": 5}


def test_search_envelope_shape(client):
    resp = client.post(
        "/api/view/search?query=test&top_k=3",
        json={"user_id": QQ, "app_id": "astrbot", "project_id": "default"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert set(body) == {"data", "meta"}
    assert body["data"] == {"episodes": [], "profiles": []}  # mock passthrough
    assert body["meta"] is None
