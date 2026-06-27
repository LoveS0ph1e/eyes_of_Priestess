"""前端静态托管的路径解析 + 穿越防护测试（_safe_spa_target）。

只读托管 build 产物时，catch-all 用此 helper 解析请求路径。安全要点：
解析后必须仍在 build 根内，``../`` 穿越一律拒（返 None → 调用方回退 index.html），
绝不发越界文件。仿静态主机 try_files $uri $uri.html。
"""

from __future__ import annotations

from app.main import _safe_spa_target


def _build_tree(tmp_path):
    (tmp_path / "index.html").write_text("index", encoding="utf-8")
    (tmp_path / "covenant.html").write_text("covenant", encoding="utf-8")
    assets = tmp_path / "_app"
    assets.mkdir()
    (assets / "app.js").write_text("js", encoding="utf-8")
    return tmp_path.resolve()


def test_serves_real_asset(tmp_path):
    root = _build_tree(tmp_path)
    assert _safe_spa_target(root, "_app/app.js") == root / "_app" / "app.js"


def test_html_fallback_for_route(tmp_path):
    """GET /covenant → covenant.html（预渲染页，仿 try_files $uri.html）。"""
    root = _build_tree(tmp_path)
    assert _safe_spa_target(root, "covenant") == root / "covenant.html"


def test_missing_returns_none(tmp_path):
    root = _build_tree(tmp_path)
    assert _safe_spa_target(root, "does/not/exist") is None


def test_empty_path_returns_none(tmp_path):
    """根路径 '' → None（调用方发 index.html）。"""
    root = _build_tree(tmp_path)
    assert _safe_spa_target(root, "") is None


def test_path_traversal_blocked(tmp_path):
    """../ 穿越解析后逃出 build 根 → None，绝不发越界文件。"""
    root = _build_tree(tmp_path)
    # 在 build 根外放一个『敏感』文件，确认穿越取不到
    (tmp_path.parent / "secret.txt").write_text("SECRET", encoding="utf-8")
    assert _safe_spa_target(root, "../secret.txt") is None
    assert _safe_spa_target(root, "../../etc/passwd") is None
