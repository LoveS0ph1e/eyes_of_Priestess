"""ReadingSteiner 记忆管理 WebUI —— 后端应用包。

独立服务（非改插件）。

分期与模块对应（详见 docs/）：
  第一期（插件层，零 EverOS 耦合）:
    - covenant_store        铭契配置读写（注意服务器配置文件含 UTF-8 BOM）
    - /epk 可视化           复用插件 everos_client 的只读/写-合法 API
    - 画像/episode 只读查看
  第二期（episode 增删改，中耦合，需 cascade）:
    - episode_editor        改 md + cascade sync
  第三期（画像直接修改，高耦合 + 高风险）:
    - profile_viewer/editor 先做快照 + 只读 diff；编辑能力待治本路线推进后评估

全局硬约束（穷举验证 EverOS 0.1.0/1.0.1 源码）:
  - EverOS 无任何改/删记忆的 HTTP API 或 CLI。HTTP 仅 add/flush/get/search/health/metrics；
    CLI 仅 server/cascade/init。唯一改删路径 = 直接改磁盘 markdown + everos cascade sync 重建索引。
  - EverOS 应用层裸奔：CORS=* + 无鉴权。写记忆入口 = 后门 → 本服务必须绑 127.0.0.1 + 自身鉴权。
  - 身份三铁律（继承自插件 core/identity.py）：user_id 必须后端受信解析/校验，绝不接受前端自由指定。
  - EverOS 引擎用 fcntl.flock，无法在 Windows 原生运行。
    故本服务对 EverOS 的真机交互只能在 Linux（服务器/WSL）上验证，开发期可用 mock。
"""
