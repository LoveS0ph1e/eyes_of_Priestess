# Palimpsest — 停机原子记忆重写框架（开发指导）

> *palimpsest*：被刮去字迹、重新书写的羊皮卷。本框架对 **EverOS 这类「md 为真相源、索引派生、污染跨派生件扩散」的记忆系统**做停机、原子、可回滚的手动重写。它把零散的运维删改（整用户删除、外科式去错误归属、画像去混合重抽、reflection 软删）收敛成同一个模型。
>
> 定位：**Sarastro WebUI 记忆编辑的批处理后端**。原独立仓 `F:\Amadeus\Palimpsest\`；二期已拷入本仓 `backend/app/modules/palimpsest/`（见 docs/06-phase2-plan.md §2/§4），逻辑零改动，CLI 入口改为 `python -m app.modules.palimpsest`。本仓 PRIVATE，故保留生产路径示例与 OM 设计引用；`plans/*.json` 的真实 QQ/生产路径不进本仓（用泛化示例，见 `plans/` 目录）。

代码：`engine.py`（单文件 MVP，纯 stdlib，py3.12）。规则：`plans/*.json`（泛化示例）。

---

## 0. 设计铁律（约束即简化）

| # | 不变量 | 理由 |
|---|---|---|
| INV-1 | **md 是唯一真相源**；lancedb 向量 + sqlite（cluster/cluster_member/memcell/md_change_state/…）+ **插件缓存**（`relationships/`、`forget/`）全是派生件 | 只写 md（+ 插件缓存），再重建索引；永不反向改 |
| INV-2 | **停机执行**（`everos` 必须 stopped），`apply`/`rollback` 用 `systemctl is-active` 自守（免 sudo 读） | 杀掉 watcher 异步重索引竞态 → 操作对系统原子 |
| INV-3 | **声明式终态，非命令式 diff** | 幂等之根：Plan 描述「想要的结果」，已达成即 no-op |
| INV-4 | **污染跨派生件**：一条错误归属 → episode + atomic_fact + foresight + 插件印象缓存 | 重写须沿派生面**co-redaction**，只改源会留残渣 |
| INV-5 | **先快照、先 dry-run、后提交；失败自动回滚** | 生产记忆零容忍不可逆损失 |
| INV-6 | **审计不落 PII 明文**：journal 存 ref + 前后哈希 + 极短脱敏窗口；正文只在受控快照里 | 私密内容不进日志、不外传 |

**非目标**：在线增量编辑（Sarastro 在线路径，需与 watcher/partition lock 协作）；通用 DB 迁移；LLM 重新抽取（可选 `rederive`，默认关——停机走确定性 co-redaction）。

---

## 1. 再审计：7 个关键事实（务必内化）

1. **插件缓存不被 EverOS 冷重建覆盖。** `rm -rf .index + restart` 只重建 EverOS 自己的索引；插件的 `relationships/<id>.md`（缓存的整体印象）在 EverOS 之外，**必须单列为 scope**（本框架作 `plugin_relationship` 伪 kind 处理）。
2. **atomic_fact / foresight 的 md 本身就是持久源**，冷重建只把它们**重新嵌入**，**不会**从 episode 重新抽取。故修正必须**逐 md co-redaction**（episode + atomic_fact + foresight 各改各的），而非「只改 episode 指望派生件自动跟新」。
3. **full 重索引会重嵌全量语料**（调用 embedding，耗时+成本，触碰 EverOS 隐藏 LLM 消耗源）。对小改是浪费。
4. **`everos cascade sync <path>` 被设计成可与运行中的服务共存**（源码原话：CLI piggybacks on the same process-wide singletons as the daemon）——但这**不代表 Palimpsest 可以在服务运行时改 md**：daemon 的 watcher 在运行期持续监听 md 变化，若在服务活着时改 md，watcher 可能与手动 `cascade sync` 产生竞态入队。**结论：incremental 仅收窄「重启后重建的范围」，不放宽「apply 须停机」这条铁律**（INV-2 对两种 reindex 模式都成立）。
5. **策略 = 默认 full（正确性优先），incremental 作可选**。`apply --keep-index` 走 incremental：停机改 md → 不 drop `.index` → 记录 `reindex_paths` → 用户重启服务 → 调用 `reindex_incremental(txn)`（一个独立步骤，只能在重启后跑，因为它需要 daemon 或自己现起的 live sqlite/lancedb 单例）逐文件跑 `everos cascade sync <path>` 强制入队。**Palimpsest 内部从不在 apply() 里直接调用 `cascade sync`**——两步必然跨越一次服务重启，不可能揉进同一次调用。
6. **RedactSpan 不动 frontmatter `entry_count`**（只改正文，天然幂等）；**DeleteEntry**（二期已实现）删整条 `<!-- entry:ID -->...<!-- /entry:ID -->` 块，**须幂等维护 `entry_count`**——已验证 episode/atomic_fact/foresight 三种 daily-log kind 的 frontmatter 字段名统一（均为 `entry_count`），一个正则通用覆盖，未做过早的按 kind 分支。
7. **校验覆盖三面**：md ∪ 索引（重启后 API search 命中=0）∪ 插件缓存。快照含 `.index`，故 full 模式回滚直接还原、免重嵌；incremental 模式回滚同样还原快照里的 `.index`（此时它是重建前的旧索引，还原后其内容仍对得上还原后的 md——因为两者是同一个快照里的一致快照对）。

### 1.1 二期审计中发现并修复的真实 bug（非纸面设计，是跑出来的）

- **txn_id 碰撞**：`txn = f"txn_{int(time.time())}"` 只有秒级精度。两次 `apply()` 在同一秒内跑完（脚本化/WebUI 触发场景完全可能）会生成**相同 txn_id**，导致第二次的快照**静默覆盖**第一次的快照文件（同名 `backups/<txn>.tar.gz`）——用户以为能回滚到某个历史点，那个点的备份其实已经不存在，`rollback` 会悄悄还原到错误的状态。**修复**：`_new_id(prefix)` = 时间戳前缀 + `uuid.uuid4().hex[:8]` 随机后缀，三处调用点（`apply` 的 txn、`rollback` 的补偿记录 txn、`reindex_incremental` 的补偿记录 txn）全部改用。已用回归测试验证：两次同秒 apply 产生不同 txn，各自快照独立、rollback 精确对应各自的时间点。
- **非 ASCII 字符导致 Windows 终端崩溃**：`_preview()`/`_preview_op()` 原用 `«»…⟶` 等字符，`print()` 到 GBK codepage 的 Windows 终端会抛 `UnicodeEncodeError`。Palimpsest 可能被直接在 Windows 开发机上跑（不只是部署到 Linux 服务器），故改为 ASCII 安全的 `[...]`/`...`/`->`。docstring/注释里的 em dash 等字符不受影响（那些从不经过 `print()`）。

---

## 2. 核心抽象（代码映射）

| 抽象 | 代码 | 职责 |
|---|---|---|
| `Layout` | `Layout` | 物理地理：owner 目录、`.index`、插件缓存路径、journal/backups |
| `KindAdapter`（一期内联为 `KIND_GLOBS`） | `KIND_GLOBS` | kind → md glob；扩展数据类型只改这里（+ 派生边） |
| `Selector` | `Selector` | 声明式定位：owner_ids × kinds × content_regex（+ 插件缓存开关） |
| `Operation` | `RedactSpan \| DeleteEntry`（union，`_apply_op`/`_preview_op` 按类型分派） | 作用于命中文件的变换。二期新增 `DeleteEntry`；`DeleteOwner`/`RewriteProfile`/`DeprecateEntry` 仍是三期候选 |
| `FileChange` | `FileChange` | 解析后的最小可逆动作，带 `pre/post_sha256`（幂等 + 漂移守卫） |
| `Plan` | `Plan` | Selector × Operation → 一组 FileChange（+ `skipped` 不可读文件列表）；`render()` 出 dry-run |
| `Transaction` | `apply()` 内联 | freeze→snapshot→write→reindex(full drop-index \| incremental 记录路径)→journal，失败 restore |
| `Verifier` | `verify()` / `verify_deleted()` | 残留扫描：`verify` 对 RedactSpan 语义（term 消失）；`verify_deleted` 对 DeleteEntry 语义（entry 块标记消失）。索引面靠重启后 API search 另证 |
| `IncrementalReindex` | `reindex_incremental()` | 独立于 apply() 的第二步：读 journal 里某 incremental txn 的 `reindex_paths`，逐个跑 `everos cascade sync <path>`。只能在服务重启后调用 |

> 二期审计后决定**不**把 `KIND_GLOBS` 升级为完整 `KindAdapter` 协议 —— episode/atomic_fact/foresight 三种 kind 的 entry 块格式（`<!-- entry:ID -->` + frontmatter `entry_count`）已验证统一，一个正则通用覆盖三者，专门的 per-kind parse/serialize 协议在当前需求下是过早抽象。`DerivedGraph`（派生边自动传播）留待真正需要多 kind 联动删除时再引入。

---

## 3. 接口（Python / CLI / 声明式 Plan）

**Plan（JSON，幂等载体）** — 示例见 `plans/example-redact-span.json`（占位符化，非生产数据）：
```json
{
  "layout": {"everos_root": "~/.everos",
             "plugin_relationships": "~/qqbot/astrbot/data/plugin_data/astrbot_plugin_readingsteiner/relationships"},
  "selector": {"owner_ids": ["<owner_id>"],
               "kinds": ["episode","atomic_fact","foresight"],
               "include_plugin_caches": true, "content_regex": "<misattributed_term>"},
  "operation": {"type":"redact_span","pattern":"(?:,\\s?|、)\\s*<misattributed_term>","replacement":""},
  "verify_term": "<misattributed_term>"
}
```

**CLI**（二期起，模块化调用；见 `__main__.py`）：
```
python -m app.modules.palimpsest plan     plan.json          # dry-run：打印 diff，零写入（服务可在线，读-only）
python -m app.modules.palimpsest apply    plan.json [--yes]  # 快照→改 md→drop .index→journal（须停机）
python -m app.modules.palimpsest apply    plan.json --keep-index   # incremental：不 drop .index，仅记录 reindex_paths
python -m app.modules.palimpsest verify   plan.json          # 残留扫描（RedactSpan 走 term；DeleteEntry 走 verify_deleted）
python -m app.modules.palimpsest reindex-incremental <txn>   # 重启服务后：对该 txn 的 reindex_paths 逐个 cascade sync
python -m app.modules.palimpsest rollback <txn>              # 还原某事务快照（须停机）
python -m app.modules.palimpsest journal                       # 审计流水
```

`DeleteEntry` 的 plan 示例见 `plans/example-delete-entry.json`（`entry_ids` 用 md 标记里的裸 `entry_id`，**不是** EverOS API 返回的复合 `id`——API 的 `id` 形如 `{owner_id}_{entry_id}`，调用方需先剥离 owner 前缀）。

**Python**：`Palimpsest(Layout(...)).plan(Selector(...), RedactSpan(...))` → `.apply(...)`。

---

## 4. 操作流程（停机原子事务）

**full 模式**：
```
0 FREEZE     用户 sudo systemctl stop everos；apply 内 _guard_stopped() 自检 is-active!=active
1 PLAN       dry-run：Selector→文件→_apply_op(RedactSpan|DeleteEntry)→FileChange（带前后哈希）；打印 diff
2 SNAPSHOT   tar.gz owner 目录 + 插件缓存 + 整个 .index → .palimpsest/backups/<txn>.tar.gz
3 WRITE      逐文件：现态哈希==post→跳过(幂等)；==pre→改(temp+rename 原子);皆非→中止(漂移)
4 REINDEX    rmtree(.index) —— 重启后冷重建
5 JOURNAL    追加 Receipt 到 .palimpsest/journal.ndjson（reindex="index_dropped"）
6 THAW       用户 sudo systemctl start everos（冷重建）
7 VERIFY     palimpsest verify/verify_deleted（md∪缓存=0）+ API search scoped owner=0
```

**incremental 模式**（`--keep-index`，步骤 4/6 之间多一步）：
```
0-3 同上（步骤 2 的快照仍含 .index —— 此时是重建前的旧索引）
4 REINDEX    保留 .index；journal 记 reindex="incremental_pending" + reindex_paths=[改过的文件]
5 JOURNAL    追加 Receipt
6 THAW       用户 sudo systemctl start everos（不冷重建，索引暂时对不上新 md）
6.5 SYNC     python -m app.modules.palimpsest reindex-incremental <txn> —— 逐 reindex_paths 跑 `everos cascade sync <path>`
7 VERIFY     同上；若残留 → 手动升级 full（drop .index + 再次 restart）
```

失败任一步 → `_restore(backup)` 自动回滚（还原 md+缓存+.index）+ journal 记 rolled_back。

---

## 5. 幂等性

- **声明式终态**：Plan 描述终态；`is_empty()`（无可改文件）即 no-op。重跑同一 plan = 0 动作。
- **pre/post 双哈希守门**：每个 FileChange 记应用前应有哈希与应达终态哈希。现态==post→跳过；==pre→改；皆非→漂移中止（有人手改过，不蒙头覆盖）。
- `RedactSpan` 天然幂等：目标短语已不存在 → `subn` 命中 0 → 该文件不入 plan。

---

## 6. 回滚

- **粗（快照）**：还原 `backups/<txn>.tar.gz`（含 md + 插件缓存 + `.index`）—— 总可用、最稳兜底；apply 中途异常的自动回退。
- **细（逐 op 逆操作）**：二期——journal 每动作带 `inverse`，逆序重放，支持回滚指定事务/部分回滚。
- 索引随 md 走：还原 md 后还原 `.index`（快照已含）或重跑 reindex，二者等价。回滚本身写补偿 Receipt。

---

## 7. 日志 / 审计

- **append-only `.palimpsest/journal.ndjson`**：每事务一行 `{txn, ts, actor, plan, status, backup, reindex, changes:[{path,kind,owner,n,pre,post}]}`。只追加，回滚写补偿行。
- **PII 安全（INV-6）**：journal 只存 ref + 前后哈希前缀 + `_preview()` 的极短脱敏窗口；完整正文只在受控 tar 快照里。
- **Verifier 后置断言随 Receipt 落库**：终态残留=0、计数自洽（RedactSpan 不动 entry_count）、无孤儿。

---

## 8. 扩展（三个扩展点，不动引擎核心）

1. **新数据类型**：加 `KIND_GLOBS[kind]=glob`（一期）；二期实现 `KindAdapter`（+ 派生边 `derives_to`）。`knowledge`/`agent_case`/`agent_skill` 各一个。
2. **新清理规则**：新 `plans/*.json`（Selector + Operation）。规则库：`redact-attribution`（本次）、`purge-owner`、`debloat-profile`、`dedup-entries`、`fix-language`。新动作类型 = 新 `Operation`（实现 `from_dict` + plan 语义 + 逆操作）。
3. **新派生边**：二期往 `DerivedGraph` 注册 `(source_kind, derived_kind, link_resolver)`，传播器据图自动扩边——把「改 episode 要连带查 atomic_fact/foresight/插件印象」变成数据。

---

## 9. 五种历史操作 → 同一框架（自检：抽象够用）

| 历史操作 | Palimpsest 表达 | 状态 |
|---|---|---|
| 外科去错误归属（一期真机验证） | `Selector(owner, [ep,af,fs]+cache, regex=<term>)` × `RedactSpan` | ✅ 已实现、已真机验证 |
| 整条删某 episode | `Selector(owner,[episode])` × `DeleteEntry(entry_ids={…})`（幂等维护 entry_count） | ✅ 二期已实现、smoke test 覆盖 |
| 整用户删除 | `Selector(owner)` × `DeleteOwner`，reindex=full | 三期候选 |
| 画像去混合重抽 / debloat | `Selector(owner, [profile])` × `RewriteProfile` | 三期候选 |
| reflection 软删原件 | `DeprecateEntry(replaced_by=merged_id)`，复用 `deprecated_by` 原语 | 三期候选（EverOS 自己的 Reflection 已有这个能力，Palimpsest 未必需要重造） |

---

## 10. 路线

- **一期**：MVP = RedactSpan + 插件缓存 + tar 快照 + full reindex + journal + verify。**用真实任务验证并落地生产。**
- **二期**：`DeleteEntry`（entry 级删除 + entry_count 维护）+ incremental reindex（`--keep-index` + `reindex-incremental` 两步式）+ `Operation` union 重构（`_apply_op`/`_preview_op` 分派，未引入 ABC/Protocol——两个实现不构成过早抽象的理由）。**审计中额外揪出并修复一个 txn_id 碰撞的真实数据完整性 bug**（见 §1.1）。决定不做 `KindAdapter`/`DerivedGraph`（三种 daily-log kind 的 entry 格式已验证统一，暂无需要）。**拷入 Sarastro WebUI 仓 `backend/app/modules/palimpsest/`（本次），CLI 入口改模块化调用，逻辑零改动。**
- **三期**：`DeleteOwner`/`RewriteProfile`/`DeprecateEntry`（如有需要）；逐 op 逆操作（当前回滚只有「整快照还原」一种粒度）；在线路径复用同一 Plan/Receipt 模型（停机锁 → 与 watcher 协作的 partition lock + md_change_state 协调）。
