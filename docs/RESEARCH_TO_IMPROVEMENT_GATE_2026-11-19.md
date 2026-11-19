# Research → Improvement Gate（2026-11-19）

## 本轮增量

- 能力雷达现会从最近时间窗内的 Research Wiki 实验记录提取 `refutes`、`inconclusive`、`failed` 结果，形成最多 50 条有来源 ID 的研究跟进候选。
- 候选保留实验节点 ID、run ID、hypothesis ID 与结果方向，便于回到原始实验核查。
- 此类记录只进入 `research_followups`，标记 `candidate_only=true`、`engineering_proposal_created=false`；不会自动创建工程改进提案、修改实验结论或变更知识节点。
- 研究候选下一步要求检查实验设计、测量误差、反例及替代解释，再决定是否设计新实验。
- 既有能力雷达对操作失败、测试失败、明确用户反馈等结构化信号的审批提案流程保持不变。

## 验收边界

- 新增测试验证反驳型实验能产生研究跟进候选、保留来源关联，并且不会生成工程提案或改写原实验记录。
- 这只是研究失败/不确定结果到后续研究任务的桥接，不代表研究结论已验证，也不代表自动自我升级。
