"""EverOS 对接的固定契约常量。

app_id/project_id 不是可配置项：插件固定用 astrbot/default（镜像插件 core 的
DEFAULT_APP_ID/DEFAULT_PROJECT_ID），且在 EverOS 磁盘上映射为 default_project 目录
（见 everos MemoryRoot 约定）。集中一处，避免字面量散落导致三铁律相关的检索参数/
路径段不一致——做成 env 配置反而是过度设计（这两个值随插件契约固定，不该可调）。
"""

from __future__ import annotations

DEFAULT_APP_ID = "astrbot"
DEFAULT_PROJECT_ID = "default"
