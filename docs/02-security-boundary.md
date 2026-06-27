# 02 · 安全边界（P0，硬约束）

> EverOS 应用层裸奔（CORS=`*` + 无鉴权）。WebUI 能改记忆 = 绕过所有隔离铁律的后门。
> 下列每一条都不是可选项，骨架期已用冒烟测试守住底线。

## 1. 网络边界

- **只绑 `127.0.0.1`**。`app/main.py` lifespan 启动守卫：host 非 127.0.0.1 直接 `RuntimeError` 拒启。前端 `vite.config.ts` 同样强制 127.0.0.1。
- **绝不 0.0.0.0**。沿用 AstrBot 范式：访问经 SSH 端口转发。
- 服务器侧 iptables 仅放行 127.0.0.1。

## 2. 自身鉴权

- `WEBUI_AUTH_SECRET` 环境变量注入强随机值。**绝硬编码、绝不进 git**。
- 未配置（空串）→ 写接口一律 503（`auth.require_auth`），拒绝裸奔。
- 未登录访问写接口 → 401（细化阶段实现，桩已占位）。
- 鉴权校验常量时间比较，防时序侧信道。
- CORS 白名单仅 `http://127.0.0.1:5173` / `localhost:5173`，**绝不 `*`**（EverOS 是反面教材）。

## 3. 身份三铁律（继承插件 `core/identity.py`）

- 铁律 1：`user_id` 必须后端受信解析/校验，**绝不接受前端自由指定**拼成路径段。`identity_resolver.resolve` 是唯一入口。
- 铁律 2：取不到有效 `user_id` 返回错误（WebUI 侧 `IdentityResolutionError` → 4xx），**绝不回退 `default`**。桩已拒 `""`/`default`/`../`。
- 铁律 3：检索只用单一 `user_id`，绝不轮询 `[uid, "default"]`。

## 4. 写操作可追溯可回滚

- 每次写操作（covenant 增删、episode 增删改、画像编辑）落 `audit_log`：谁、何时、改了什么、备份在哪。
- 写前备份原 md/配置（铭契改前备份插件配置 JSON；episode 画像改前备份 user.md）。

## 5. 数据格式风险（第一期铭契已踩）

- 插件配置文件含 **UTF-8 BOM**：读 `utf-8-sig`（吞 BOM），写 `utf-8`（不回写）。
- `eternal_covenant` 值是 **JSON 字符串**，值内引号需转义。
- 画像 frontmatter YAML 改错 → reader 解析崩 → 注入降级（机器人失忆）。第三期画像编辑前必须 YAML schema 校验。

## 冒烟测试守底线

`backend/tests/test_security_boundaries.py`：
- 写接口未配鉴权 → 503
- 只读接口未登录可访问（鉴权可选）
- user_id 空/default/路径穿越 → 拒
- host 非 127.0.0.1 → 拒启

这四条是 regress 防线，细化阶段不得破坏。