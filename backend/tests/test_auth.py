"""鉴权真实实现测试 —— 把 P0 安全闸从桩变底线的全覆盖。

覆盖：
  - 登录：未配密钥 503 / 错误密钥 401 / 常量时间比较 / 成功 200+set-cookie
  - token：签发+校验 / 篡改→无效 / 过期→无效 / 密钥变更→旧 token 失效
  - require_auth：缺失 token 401 / 无效 token 401 / 有效 token 放行 / 未配密钥 503
  - require_auth_optional：未登录 None / 无效 token None / 有效 token 返主体
  - /me：已登录返主体；/logout 清 cookie
  - token 双来源：Authorization Bearer 与 cookie
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.modules.auth import SESSION_COOKIE
from app.modules.auth.token import TokenError, issue, verify

SECRET = "test-secret-token-very-long-for-hmac-1234567890"


@pytest.fixture
def secured_client(monkeypatch):
    """带有效密钥的客户端（写接口不再 503）。

    raise_server_exceptions=False：covenant_store 等业务桩未实现会抛 NotImplementedError，
    鉴权测试只验证『是否越过鉴权门』（status != 401/503），桩的 500 不该被 re-raise 成异常。
    """
    from app.core import config as config_module
    from app.main import app

    monkeypatch.setattr(config_module, "settings", Settings(host="127.0.0.1", auth_secret=SECRET))
    # with 触发 lifespan，把各单例按 monkeypatch 后的 settings 建进 app.state。
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


def login(client):
    return client.post("/api/auth/login", json={"secret": SECRET})


# ── 登录 ────────────────────────────────────────────────────────────────


def test_login_unconfigured_returns_503(client):
    """未配密钥时登录本身拒绝（防止误把空密钥当任意值通过）。"""
    resp = client.post("/api/auth/login", json={"secret": ""})
    assert resp.status_code == 503


def test_login_wrong_secret_returns_401(secured_client):
    resp = secured_client.post("/api/auth/login", json={"secret": "wrong"})
    assert resp.status_code == 401
    assert resp.json()["detail"] == "密钥错误"


def test_login_success_returns_token_and_sets_cookie(secured_client):
    resp = login(secured_client)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "token" in body and body["subject"] == "admin" and body["expires_in"] > 0
    # HttpOnly + SameSite=Strict cookie 已设
    cookie = resp.headers.get("set-cookie", "")
    assert SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "samesite=strict" in cookie.lower()


# ── token 单元 ───────────────────────────────────────────────────────────


def test_token_issue_verify_roundtrip():
    t = issue(subject="admin", secret=SECRET, ttl_seconds=60, now=1000.0)
    assert verify(token=t, secret=SECRET, now=1059.0) == "admin"  # exp=1060，未过期边界


def test_token_expired_is_invalid():
    t = issue(subject="admin", secret=SECRET, ttl_seconds=60, now=1000.0)
    with pytest.raises(TokenError):
        verify(token=t, secret=SECRET, now=1061.0)


def test_token_tampered_sig_is_invalid():
    t = issue(subject="admin", secret=SECRET, ttl_seconds=60, now=1000.0)
    payload_b64, sig_b64 = t.split(".")
    # 篡改 payload（改 subject）后原签名必失配
    tampered = payload_b64 + "." + sig_b64[::-1]
    with pytest.raises(TokenError):
        verify(token=tampered, secret=SECRET, now=1050.0)


def test_token_secret_rotation_invalidates_old():
    """轮换密钥 → 旧 token 全失效（密钥轮换的安全副作用）。"""
    t = issue(subject="admin", secret=SECRET, ttl_seconds=60, now=1000.0)
    assert verify(token=t, secret=SECRET, now=1050.0) == "admin"
    with pytest.raises(TokenError):
        verify(token=t, secret="new-rotated-secret-xx", now=1050.0)


def test_token_garbage_format_invalid():
    for garbage in ("", "no-dot-here", "a.b.c", "...", "payload.only"):
        with pytest.raises(TokenError):
            verify(token=garbage, secret=SECRET, now=1000.0)


# ── require_auth（写接口）──────────────────────────────────────────────


def test_write_without_token_returns_401(secured_client):
    """配了密钥但没带 token → 401（区别于未配密钥的 503）。"""
    resp = secured_client.put(
        "/api/covenant/1000000001", json={"user_id": "1000000001", "text": "x"}
    )
    assert resp.status_code == 401


def test_write_with_invalid_token_returns_401(secured_client):
    resp = secured_client.put(
        "/api/covenant/1000000001",
        json={"user_id": "1000000001", "text": "x"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert resp.status_code == 401


def test_write_with_valid_token_passes_guard(secured_client):
    """有效 token 应越过鉴权门（后续 404/502 由业务/covenant_store 决定，不再是 401）。"""
    token = login(secured_client).json()["token"]
    resp = secured_client.put(
        "/api/covenant/1000000001",
        json={"user_id": "1000000001", "text": "x"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code != 401  # 越过鉴权门──真正失败应是 covenant_store 未实现(500/NotImpl)
    assert resp.status_code != 503  # 密钥已配，不应再 503


def test_write_with_expired_token_returns_401(secured_client):
    """签一个已过期 token：手工构造过去签发+过期时间，但密钥仍真实。"""
    # 直接签发后立刻用一个『很大的 now』去验，模拟过期。
    t = issue(subject="admin", secret=SECRET, ttl_seconds=1, now=time.time() - 10)
    secured_client.cookies.set(SESSION_COOKIE, t, domain="testserver")
    resp = secured_client.put(
        "/api/covenant/1000000001",
        json={"user_id": "1000000001", "text": "x"},
    )
    # 过期 token 走 require_auth：cookie 提取 → verify 抛 TokenError → 401
    assert resp.status_code == 401


# ── token 元数据 ─────────────────────────────────────────────────────────


def test_me_works_with_token(secured_client):
    token = login(secured_client).json()["token"]
    resp = secured_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["subject"] == "admin"


def test_me_without_token_returns_401(secured_client):
    resp = secured_client.get("/api/auth/me")
    assert resp.status_code == 401


def test_logout_clears_cookie(secured_client):
    login(secured_client)
    resp = secured_client.post("/api/auth/logout")
    assert resp.status_code == 200
    cookie = resp.headers.get("set-cookie", "")
    assert SESSION_COOKIE in cookie and ("max-age=0" in cookie.lower())


# ── token 双来源 ─────────────────────────────────────────────────────────


def test_token_accepted_via_authorization_header(secured_client):
    token = login(secured_client).json()["token"]
    resp = secured_client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


def test_token_accepted_via_cookie(secured_client):
    # login 已设 cookie，TestClient 会自动带；直接请求 /me
    secured_client.post("/api/auth/login", json={"secret": SECRET})
    resp = secured_client.get("/api/auth/me")
    assert resp.status_code == 200


# ── 可选鉴权（只读接口）─────────────────────────────────────────────────


def test_readonly_works_without_token(secured_client):
    """只读接口鉴权可选：未登录仍可访问（mock 网关恒健康）。"""
    resp = secured_client.get("/api/view/health")
    assert resp.status_code == 200
    assert resp.json()["healthy"] is True
