"""安全边界冒烟测试 —— 验证硬约束在骨架期就成立（plan『安全』验收项）。

这些是无论如何不能 regress 的底线：
  - 未配鉴权时写接口拒绝裸奔（503）。
  - 启动 host 非 127.0.0.1 应被拦。
  - user_id 不可信（空/default/路径穿越）应被 identity_resolver 拒。

具体业务测试随细化阶段补。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def test_root_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_write_rejected_when_auth_unconfigured(client):
    """未配 WEBUI_AUTH_SECRET 时写接口必须 503（拒绝裸奔）。"""
    resp = client.put("/api/covenant/1000000001", json={"user_id": "1000000001", "text": "x"})
    assert resp.status_code == 503


def test_readonly_health_works_without_auth(client):
    """只读接口鉴权可选，未登录可访问（mock 网关恒健康）。"""
    resp = client.get("/api/view/health")
    assert resp.status_code == 200
    assert resp.json()["healthy"] is True


def test_identity_rejects_unsafe_user_id():
    from app.modules.identity_resolver import IdentityResolutionError, resolve

    with pytest.raises(IdentityResolutionError):
        resolve("")
    with pytest.raises(IdentityResolutionError):
        resolve("default")
    with pytest.raises(IdentityResolutionError):
        resolve("../etc/passwd")


def test_app_rejects_non_localhost_host(monkeypatch):
    """host 非 127.0.0.1 时 lifespan 必须拒绝启动。"""
    from app.core import config as config_module
    from app.core.config import Settings
    from app.main import app

    # 用一个 host=0.0.0.0 的实例替换模块单例，测 lifespan 启动守卫。
    monkeypatch.setattr(config_module, "settings", Settings(host="0.0.0.0", auth_secret="x"))
    with pytest.raises(RuntimeError, match="安全边界"):
        with TestClient(app):
            pass
