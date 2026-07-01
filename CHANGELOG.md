# 更新日志

WebUI 版本系列代号 **Sarastro**。遵循[语义化版本](https://semver.org/lang/zh-CN/)，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/)。

## [0.2.0] · 第二期：episode 删除

覆盖第二期能力：episode 增删改的第一块（删除），中耦合，需与 EverOS 向量索引同步。

### 新增
- **episode 删除**：包 Palimpsest 批处理引擎（`backend/app/modules/palimpsest/`），停机原子写 +
  快照可回滚 + journal 审计流水。三端点：`GET .../plan` dry-run 预览、`DELETE ...` 真正执行、
  `POST .../reindex/{txn}` 手动同步 incremental 重索引。
- **`everos_gateway` ABC 扩写**：一期只读 4 方法 → 二期共 8 方法，新增 `read_user_markdown` /
  `write_user_markdown`（乐观锁，版本冲突返 409）/ `cascade_sync` / `is_everos_stopped`。
- **前端**：episode 列表加删除交互，确认弹窗展示改动预览、重索引模式（incremental / full）可选。

### 说明
- 停/启 EverOS 服务仍由运维手动 SSH 完成——WebUI 只组 plan + dry-run，不持 sudo/SSH 私钥。
- 第三期（画像编辑）见 `docs/03-phase-plan.md`；对应后端模块（`profile_viewer`）仍为有意预留的桩。

## [0.1.0] - 2026-06-27 · Sarastro 首发

首个公开版本，覆盖第一期能力。

### 新增
- **铭契编辑**：列表 / 新增 / 修改 / 删除（空文本 = 删除该键）；写操作前置鉴权并落审计日志、写前备份配置。
- **`/epk` 检索可视化**：复用 EverOS 只读检索能力，浏览器内预览召回。
- **画像 / episode 只读查看**：分页浏览，鉴权可选（未登录可看）。
- **安全边界**：仅绑 `127.0.0.1`、自身鉴权（HMAC 无状态 token、常量时间比较）、CORS 白名单、身份三铁律、写前备份 + 审计；启动守卫与冒烟测试守住底线。
- **部署形态**：FastAPI 同源静态托管前端构建产物（单端口、免 CORS、省内存）；提供 systemd 单元样例与 `.env` 样例；经 SSH 隧道访问。

### 说明
- 第二期（episode 增删改）与第三期（画像快照 + 只读 diff）见 `docs/03-phase-plan.md`；对应后端模块当前为**有意预留的桩**（契约未在真机坐实前不固化签名），非缺失。
