# 03 · 分期路线（按耦合度递进）

> 每期独立可上线。耦合度随期数递增：零 → 中 → 高。

## 第一期 · 插件层（零 EverOS 耦合）

**范围（待你细化时确认）**：铭契编辑 + `/epk` 可视化 + 画像/episode 只读查看。

| 能力 | 后端模块 | 路由 | EverOS 耦合 |
|---|---|---|---|
| 铭契增删改 | `covenant_store` | `/api/covenant` | 零（插件自有 JSON 配置） |
| 画像只读 | `everos_gateway.get_profile` | `/api/view/profile/{uid}` | 只读 HTTP `get` |
| episode 只读列举 | `everos_gateway.list_episodes` | `/api/view/episodes/{uid}` | 只读 HTTP `get` |
| 检索预览 | `everos_gateway.search` | `/api/view/search` | 只读 HTTP `search` |
| 正常状态/健康 | `everos_gateway.health` | `/api/view/health` | 只读 HTTP `/health` |

**关键注意**：铭契配置文件带 UTF-8 BOM（`utf-8-sig` 读、`utf-8` 写），值内引号转义。

**验收**：铭改完→真机发消息确认【永恒铭契】块随之变；`/epk` 各命令 WebUI 与 QQ 内行为一致。

## 第二期 · episode 增删改（中耦合）

**范围**：补上 EverOS 缺失的 episode 删除/修改。

机制：定位用户 episode 的 md → 用 EverOS `MarkdownWriter` 改/删 marker entry → `everos cascade sync <path>`。

**核心耦合点**（就是当初要弄清的）：删/改 episode **必须 cascade sync**，否则 md 与 LanceDB 不一致（改了 md 但向量索引还是旧的→召回错乱）。片子档「中耦合」的本质 = 不需手动同时动三存储，但必须能调 cascade。

**仍开放决策**：cascade sync 怎么调——
- 子进程跑 `everos cascade sync`：松耦合，依赖 CLI 可用。
- import EverOS 包内 `CascadeOrchestrator`：紧耦合，免进程开销，EverOS 升级即裂。

第二期落地时定，现 `everos_gateway` ABC 第一期只读 4 方法，写/cascade 方法不进 ABC（反过早抽象），第二期决策定了一并扩签名。

**验收**：删/改一条 episode→`get` 确认条目消失/变更→`search` 确认召回随之变。

## 第三期 · 画像直接修改（高耦合+高风险，先评估再做）

**范围**：可视化编辑画像 summary/explicit/implicit（缓解画像 flush 退化）。

**必须先告知用户的风险**：
1. 手改 summary 后下次 EverOS flush 用 LLM 整体重写 → 改动可能丢失。**治标**。
2. frontmatter YAML 改错 → reader 解析崩 → 注入降级（机器人失忆）。
3. 改 md 必须 cascade sync。

**治本路线（均超出 WebUI，WebUI 只能治标「改得快」）**：
① 扩大铭契固定层覆盖；② 注入层 `_render_profile` 对 summary/explicit 高重合去重；③ 改 EverOS flush 提取 prompt。

**本期范围（plan 决策）**：先只做「画像快照备份 + 只读 diff 查看」——看 flush 把画像改成了什么，坐实风险并验证快照价值。编辑能力待治本路线推进后评估。

**验收**：画像快照→手动触发一次 flush→diff 看 EverOS 把画像改成了什么。

## 给大模型当执行层

v4-pro 等可按本三期 + 模块契约产出代码。但三处**真机验证 + 人工审查不可省**：
- `everos_gateway`（私有格式）
- `auth`（鉴权，漏一处=记忆后门）
- 画像编辑（flush 赛跑语义）