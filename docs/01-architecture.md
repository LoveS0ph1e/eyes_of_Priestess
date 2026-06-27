# 01 · 架构

## 后端 7 模块

| 模块 | 期 | 职责 | 耦合 |
|---|---|---|---|
| `auth` | 1 | 登录鉴权，所有写操作前闸。未配密钥→写接口 503 | 零 |
| `identity_resolver` | 1 | 界面传入 user_id → 受信身份。继承三铁律 | 零 |
| `everos_gateway` | 1→3 | 对 EverOS 所有交互的归口。只读走 HTTP，写走 md+cascade | 唯一耦合点 |
| `covenant_store` | 1 | 铭契配置读写（插件 JSON，带 BOM） | 零（插件自有配置） |
| `episode_editor` | 2 | episode 增删改（依赖 gateway 的 md+cascade） | 中 |
| `profile_viewer` | 3 | 画像只读查看 + 快照 diff（编辑待评估） | 高 |
| `audit_log` | 1+ | 记录每次写操作（谁/何时/改了什么/备份在哪） | 贯穿 |

## `everos_gateway` 抽象（第一期：只读 4 方法；写/cascade 第二期再扩）

```
HTTPEverOSGateway   ── 读 httpx 调 HTTP；写 md + 子进程 everos cascade sync
InProcessEverOSGateway ── import everos 包 (MarkdownReader/Writer/MemoryRoot + CascadeOrchestrator)
MockEverOSGateway   ── 开发期桩（Windows 无 EverOS）
```

接口在 `backend/app/modules/everos_gateway/__init__.py`。**第一期 ABC 只声明只读 4 方法**
（health/get_profile/list_episodes/search）；写/cascade 方法（read_user_markdown /
write_user_markdown / cascade_sync）刻意不进 ABC——cascade 调用方式是 plan「仍开放」项，
未定即不固化签名（反过早抽象）。第二期决策定了再连签名一起扩。

## EverOS 包事实（读码坐实）

> 穷举验证版本：EverOS 0.1.0 / 1.0.1（读源码坐实）。
> 下述每条均对这两个版本读码确认，非凭记忆；EverOS 升级时此处需复核。

- `core/persistence/markdown/{reader,writer}.py`：磁盘 markdown（frontmatter YAML + marker entry）是单一事实源。Writer 提供 temp+`os.replace` 原子写、per-path asyncio 锁、`fcntl.flock`、path-traversal 防护。
- `core/persistence/memory_root.py`：`MemoryRoot.users_dir(app_id, project_id)` 解析用户记忆路径。app/project 目录约定 `default`→`default_app`/`default_project`。
- `entrypoints/cli/commands/cascade.py`：`cascade sync [path]`——可选先强制入队单文件再排空队列。注释原话：「fix md and re-save to recover」。
- `entrypoints/api/routes/{get,search,memorize,health,metrics}.py`：HTTP 端点，无 delete/update。

## 路由分组（`backend/app/api/routes/`）

- `auth.py` — `/api/auth/login`、`/api/auth/logout`
- `covenant.py` — `/api/covenant`（GET 列表 / PUT 新增覆盖 / DELETE）
- `readonly.py` — `/api/view/health`、`/api/view/profile/{uid}`、`/api/view/episodes/{uid}`、`/api/view/search`；episode 编辑占位返回 501
- `audit.py` — `/api/audit/recent`

## 验证（每期验收，见 plan「验证方式」）

- 第一期：铭改完→真机发消息确认【永恒铭契】块随之变；`/epk` 各命令与 QQ 内行为一致。
- 第二期：删/改 episode→`get` 确认条目消失/变更→`search` 确认召回随之变（证明 cascade 已同步）。
- 第三期：画像快照→手动 flush→diff 验证「会被回退」+ 快照价值。
- 安全（贯穿）：未登录访问写接口→401；`ss -tlnp` 无 0.0.0.0。