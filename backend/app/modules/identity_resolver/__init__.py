"""身份解析器 —— 继承插件 core/identity.py 的三铁律。

铁律 1：user_id 必须来自受信来源（插件侧是 event.get_sender_id()），
        WebUI 侧由后端解析/校验，绝不接受前端自由指定的 user_id。
铁律 2：取不到有效 user_id 时跳过操作，绝不回退 "default"。
铁律 3：检索只用单一 user_id，绝不轮询 [uid, "default"]。

WebUI 与插件的身份来源不同：插件从 AstrBot event 拿真实 QQ 号；
WebUI 的『目标用户』来自运维人员在界面上的选择。故此处必须把
『界面传入的 user_id』视为不可信输入，经白名单/校验后才能用于
记忆路径段或 EverOS 检索——绝不让前端直接拼成磁盘路径。

本文件仅提供接口桩，具体校验策略（已知用户白名单 / 与 EverOS get
交叉验证存在性）留给细化阶段。
"""

from __future__ import annotations

from dataclasses import dataclass

from ...core.constants import DEFAULT_APP_ID, DEFAULT_PROJECT_ID


@dataclass(frozen=True)
class ResolvedIdentity:
    """经后端校验、可安全用于记忆操作的身份。"""

    user_id: str  # 已校验为非空、非 'default' 的真实标识
    app_id: str
    project_id: str
    is_group: bool = False  # WebUI 主要按私聊用户操作


class IdentityResolutionError(ValueError):
    """user_id 不可信或无效（空、回退 default、含路径穿越字符等）。"""


def _is_safe_user_id(raw: str) -> bool:
    """最小校验：非空、非 'default'、无路径分隔符/穿越片段。

    TODO(细化阶段): 接入已知用户白名单或与 EverOS get 交叉验证存在性。
    """
    if not raw or raw == "default":
        return False
    return not any(ch in raw for ch in ("/", "\\", "..", os_sep()))


def os_sep() -> str:
    """避免在模块顶层 import os 仅取 sep；隔离便于测试。"""
    import os

    return os.sep


def resolve(
    raw_user_id: str | None,
    *,
    app_id: str = DEFAULT_APP_ID,
    project_id: str = DEFAULT_PROJECT_ID,
) -> ResolvedIdentity:
    """把界面传入的 user_id 解析为受信身份。

    违反任一铁律 → IdentityResolutionError，调用方应返回 4xx 而非落盘。
    TODO(细化阶段): 实现完整校验链（白名单 + EverOS 存在性交叉验证）。
    """
    if raw_user_id is None or not _is_safe_user_id(raw_user_id):
        raise IdentityResolutionError(f"不可信的 user_id: {raw_user_id!r}")
    return ResolvedIdentity(
        user_id=raw_user_id,
        app_id=app_id,
        project_id=project_id,
    )
