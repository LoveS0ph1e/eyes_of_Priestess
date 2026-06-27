"""画像查看器 —— 第三期（高耦合 + 高风险，先做查看 + 快照）。

⚠️ 必须先告知用户的风险：
  1. 手改画像 summary 后，下次 EverOS flush 用 LLM 整体重写 → 改动可能丢失（治标）。
  2. 画像存 frontmatter YAML，格式错误 → reader 解析崩 → 注入降级（机器人失忆）。
  3. 改 md 必须 cascade sync，否则向量层与画像不一致。

本期范围（plan 决策）：先只做「画像快照备份 + 只读 diff 查看」——看 flush 把
画像改成了什么，坐实「会被回退」的风险并验证快照价值。编辑能力待治本路线
（扩铭契 / 注入层去重 / 改 flush prompt）推进后再评估是否值得做。

⚠️ 第一期仅留本设计说明：class/dataclass/方法签名（ProfileSnapshot、ProfileViewer）
刻意不固化（反过早抽象，同 everos_gateway ABC）。快照存储格式 / diff 形态待第三期开工
据真机画像结构再定。
"""
