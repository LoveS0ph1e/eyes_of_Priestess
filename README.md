# ReadingSteiner 记忆管理 WebUI

> 版本系列 **Sarastro** · v0.2.0 · 许可 [Apache-2.0](LICENSE)

**ReadingSteiner WebUI** 是 [AstrBot](https://github.com/Soulter/AstrBot) 记忆插件 [astrbot_plugin_readingsteiner](https://github.com/LoveS0ph1e/astrbot_plugin_readingsteiner) 的姊妹项目，为其提供记忆管理的可视化界面：**铭契编辑**、**`/epk` 检索可视化**、**画像 / episode 只读查看**。底层记忆引擎为 [EverOS](https://github.com/EverMind-AI/EverOS)。面向自建机器人的运维者，在浏览器内安全地查看与编辑机器人记忆。

> **状态**：Sarastro 0.2.0，第一期 + 第二期（episode 删除）能力已落地；第三期见[分期路线](docs/03-phase-plan.md)。

## 为什么是独立服务

WebUI 是**独立进程**，不塞进插件——故障域隔离，避开插件热重载竞态。它也不是「只转发接口」的薄层：第二、三期一旦触及画像 / episode，就必须理解 EverOS 的私有 markdown 格式与 cascade 同步，紧耦合到上游引擎。

## 核心硬约束（据 EverOS 0.1.0 / 1.0.1 源码坐实）

1. **EverOS 无任何改 / 删记忆的 API 或 CLI**。HTTP 仅 `add/flush/get/search/health/metrics`，CLI 仅 `server/cascade/init`。唯一改删路径 = 直接改磁盘 markdown + `everos cascade sync` 重建索引。
2. **EverOS 应用层无防护**：CORS=`*` + 无鉴权。WebUI 一旦能写记忆即绕过所有隔离 → 本服务必须绑 `127.0.0.1` + 自身鉴权，**绝不公网**。
3. **身份三铁律**：`user_id` 必须后端受信解析 / 校验，绝不接受前端自由指定；取不到不回退 `default`；检索只用单一 `user_id`。
4. **EverOS 不能在 Windows 原生运行**（依赖 `fcntl.flock`）。真机交互只能在 Linux（服务器 / WSL）验证；开发期后端用 `MockEverOSGateway`。

安全边界详见 [docs/02-security-boundary.md](docs/02-security-boundary.md)。

## 能力

| 能力 | 期 | 说明 |
|---|---|---|
| 铭契编辑 | 一 | 列表 / 新增 / 修改 / 删除（空文本 = 删除该键）；写前鉴权 + 备份 + 审计 |
| `/epk` 检索可视化 | 一 | 复用 EverOS 只读检索，浏览器内预览召回 |
| 画像 / episode 只读 | 一 | 分页浏览，鉴权可选（未登录可看） |
| episode 删除 | 二 | 停机原子写（Palimpsest 引擎）；plan 预览 → 停机 → 执行 → 快照可回滚 → 重索引 |

## 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| 后端 | Python 3.12 / FastAPI | 同栈复用 EverOS 持久化层与插件 `everos_client`，不跨语言重造 md 解析 |
| 前端 | Svelte 5 (runes) + Vite | 轻、产物小；runes 显式响应式更利协作、更少隐性 bug |
| 部署 | 独立进程，绑 127.0.0.1 + SSH 隧道 + 自身鉴权 | 与 AstrBot / EverOS 现有安全模型一致 |

## 三期路线（按耦合度递进，每期独立可上线）

- **第一期（零 EverOS 耦合）**：铭契编辑 + `/epk` 可视化 + 画像 / episode 只读。✅
- **第二期（episode 增删改，中耦合）**：Palimpsest 批处理引擎，停机原子写 + 快照回滚。✅ 本版
- **第三期（画像编辑，高耦合 + 高风险）**：先做快照 + 只读 diff；编辑会被 flush 回退（治标），YAML 改错致失忆，待评估。

详见 [docs/03-phase-plan.md](docs/03-phase-plan.md)。

> 第三期对应的后端模块（`profile_viewer`）当前为**有意预留的桩**——真机契约未坐实前不固化签名，非缺失。

## 目录结构

```
readingsteiner-webui/
├── backend/         FastAPI 后端（绑 127.0.0.1 + CORS 白名单）
│   ├── app/api/routes/   auth / covenant / readonly / audit
│   ├── app/modules/      auth · identity_resolver · everos_gateway · covenant_store
│   │                     · episode_editor · palimpsest · profile_viewer · audit_log
│   └── tests/            含安全边界冒烟测试
├── frontend/        Svelte 5 + Vite（runes）
├── deploy/          systemd / .env 样例
└── docs/            01-架构 · 02-安全边界 · 03-分期路线 · 04-部署
```

## 本地开发

```bash
# 后端（Windows：py -3.12；Linux：python3.12）
cd backend
python3.12 -m venv .venv
source .venv/bin/activate        # Windows: .venv/Scripts/activate
pip install -r requirements.txt -r requirements-dev.txt
pytest                           # 含安全边界冒烟测试
uvicorn app.main:app --host 127.0.0.1 --port 8761 --reload

# 前端（另开终端）
cd frontend
npm install
npm run dev                      # http://127.0.0.1:5173（Vite 代理 /api → 后端）
```

> Windows 开发机：EverOS 不能原生运行，后端走 `MockEverOSGateway`（返空）；真机数据需连 Linux 上的 EverOS。
> 一键起前后端联调：`bash scripts/dev-local.sh`（自造本地测试配置 + 临时密钥，零生产耦合）。

运行时硬约束：**Python ≥ 3.12**（低于即拒绝安装 / 解析）。

## 部署

见 [docs/04-deployment.md](docs/04-deployment.md)：以 systemd 部署到你的服务器，绑 `127.0.0.1`、经 SSH 隧道访问；前端构建产物由 FastAPI 同源托管（单端口、免 CORS）。

## 许可

[Apache-2.0](LICENSE)
