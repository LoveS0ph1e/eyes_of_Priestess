"""episode 增删改 —— 第二期（中耦合，需 cascade）。

机制：定位用户 episode 的 md 文件 → 用 EverOS 同款原子写（或直接复用其
MarkdownWriter）改/删 marker 分隔的 entry（<!-- entry:id -->）→ 触发
`everos cascade sync <path>` 重建索引。

核心耦合点：删/改 episode **必须 cascade sync**，否则 md 与 LanceDB 不一致
（改了 md 但向量索引还是旧的 → 召回错乱）。这是「中耦合」的本质。

依赖 everos_gateway 的 read/write_user_markdown + cascade_sync。

⚠️ 第一期仅留本设计说明：class/dataclass/方法签名（EpisodeEdit、EpisodeEditor）刻意
不固化——与 everos_gateway ABC「只声明只读 4 方法」同理（反过早抽象）。cascade 的具体
调用方式（命令格式、marker 约定）真机未坐实，过早定签名会固化错误假设。第二期开工时
据真机契约再落地。
"""
