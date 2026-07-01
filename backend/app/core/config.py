"""应用级配置加载。

从环境变量 / .env 读取。所有敏感项（鉴权密钥、EverOS 地址）默认绑 127.0.0.1，
绝不公网。参见 docs/02-security-boundary.md。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def _env_bool(key: str, default: str) -> bool:
    """环境变量布尔：'1'/'true'/'yes'（大小写不敏感）为真，其余为假。"""
    return os.environ.get(key, default).strip().lower() in ("1", "true", "yes")


@dataclass(frozen=True)
class Settings:
    """运行配置。生产值由环境变量注入，开发默认值仅本机可用。"""

    # ── 网络边界（P0，硬约束）──────────────────────────────────────
    # 绑 127.0.0.1，绝不 0.0.0.0。EverOS 应用层裸奔，写记忆入口不能公网。
    host: str = _env("WEBUI_HOST", "127.0.0.1")
    port: int = int(_env("WEBUI_PORT", "8761"))

    # ── 鉴权（P0，硬约束）──────────────────────────────────────────
    # WebUI 能改记忆 = 高危，即便走 SSH 隧道也必须有自身登录。
    # 生产必须通过 WEBUI_AUTH_SECRET 注入强随机值；默认空串时启动会拒绝写接口。
    auth_secret: str = _env("WEBUI_AUTH_SECRET", "")
    session_ttl_seconds: int = int(_env("WEBUI_SESSION_TTL", "28800"))  # 8h

    # ── EverOS 对接（第一期只读走 HTTP API）──────────────────────
    # 生产环境：EverOS 宿主 venv + systemd，
    # 监听 127.0.0.1:8596（非 8000）。flush 触发 v4-pro 提取约 30-50s，90s 超时防假失败。
    everos_base_url: str = _env("EVEROS_BASE_URL", "http://127.0.0.1:8596")
    everos_timeout: float = float(_env("EVEROS_TIMEOUT", "90"))
    # 网关实现选择：mock（开发期 Windows 无 EverOS，默认）| http（真机部署连 :8596）。
    # 默认 mock 避免开发机误连不存在的 EverOS；服务器部署在 .env 设 http。
    everos_gateway: str = _env("EVEROS_GATEWAY", "mock")

    # ── 插件配置对接（第一期 covenant_store 用）──────────────────
    # 指向插件在服务器上的配置文件（带 UTF-8 BOM）。
    # 开发期可指向本地样例；生产由部署注入。
    plugin_config_path: str = _env(
        "PLUGIN_CONFIG_PATH",
        "/home/youruser/qqbot/astrbot/data/config/astrbot_plugin_readingsteiner_config.json",
    )

    # ── EverOS markdown 数据根（第二三期改 md + cascade 用）──────
    # 对应 everos.core.persistence.MemoryRoot.root。例如：
    # ~/.everos/（即 /home/youruser/.everos）。app_id=astrbot、project_id=default
    # 在磁盘上映射为 default_project 目录（见 everos MemoryRoot.project_dir_name 约定）。
    # 默认留空：第一期不碰 md，避免误配置指向错误根目录；二三期上线前由部署注入。
    everos_memory_root: str = _env("EVEROS_MEMORY_ROOT", "")

    # ── everos CLI 可执行文件路径（cascade_sync 用）──────────────
    # 真机验证坐实：`everos` 装在 venv 内（~/everos/.venv/bin/everos），
    # 不在登录 shell 或 systemd 服务的默认 PATH 里；裸命令会 subprocess
    # FileNotFoundError。默认留空（与 everos_memory_root/frontend_dir 同一套"留空=
    # 未配置"约定）——消费处（main.py）留空时回退网关构造函数自己的裸名默认值，
    # 避免 .env 里 `EVEROS_BIN=`（空值）被 systemd EnvironmentFile 解析成"已设置为
    # 空字符串"而覆盖掉本该生效的裸名默认。
    everos_bin: str = _env("EVEROS_BIN", "")

    # ── audit_log 落盘位置 ────────────────────────────────────────
    audit_log_path: str = _env("WEBUI_AUDIT_LOG", "data/audit.jsonl")

    # ── 数据目录（备份/快照）────────────────────────────────────
    data_dir: Path = Path(_env("WEBUI_DATA_DIR", "data"))

    # ── covenant 只读开关（联调防误改生产）──────────────────────
    # WEBUI_COVENANT_READONLY=1 时 covenant 写接口一律拒（路由层读此拒写并 502），
    # 避免靠人记 .bak.test 兜底——代码开关胜过纪律。
    covenant_readonly: bool = _env_bool("WEBUI_COVENANT_READONLY", "0")

    # ── 前端静态托管（生产）──────────────────────────────────────
    # 指向 npm run build 产物目录（adapter-static SPA）。设了且存在 → FastAPI 同进程、
    # 同源托管前端（免 CORS、单端口、最省内存）；留空 → 不挂载（开发期前端走 vite dev）。
    frontend_dir: str = _env("WEBUI_FRONTEND_DIR", "")

    @property
    def auth_configured(self) -> bool:
        """鉴权密钥是否已配置。未配置时写接口一律 503（拒绝裸奔）。"""
        return bool(self.auth_secret)


settings = Settings()
