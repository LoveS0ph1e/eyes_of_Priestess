"""CLI 入口：``python -m app.modules.palimpsest <cmd> ...``（admin 手动 SSH 直跑）。

WebUI 路由只组 plan + dry-run（docs/06 已定），真正的 apply/rollback 由 admin
经 SSH 手动跑本 CLI——与原独立 `palimpsest.py` 脚本用法一致，只是入口换成模块。
"""

from __future__ import annotations

from .engine import main

if __name__ == "__main__":
    raise SystemExit(main())
