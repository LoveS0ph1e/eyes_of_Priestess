"""共享 pytest fixtures。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch):
    """未配密钥的客户端（用于测 503 裸奔守卫）。

    Settings 是 frozen dataclass，用整实例替换模块单例（各模块运行时从 config 模块读）。
    以 `with` 进入 TestClient 触发 main.lifespan，使各单例（covenant_store/audit_log/
    gateway）按 monkeypatch 后的 settings 建进 app.state。无 with 则 lifespan 不跑。
    """
    from app.core import config as config_module
    from app.core.config import Settings
    from app.main import app

    monkeypatch.setattr(config_module, "settings", Settings(host="127.0.0.1", auth_secret=""))
    with TestClient(app) as c:
        yield c
