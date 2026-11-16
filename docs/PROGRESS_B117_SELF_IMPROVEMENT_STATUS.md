# B-117：自我进化待办状态可观测性

日期：2026-11-13

## 本轮完成

- `AutonomousSupervisor.status()` 新增 `self_improvement` 快照：只读统计 ImprovementStore 中 proposed、researching、ready_for_review、approved、implemented、rejected、rolled_back 各状态数量，并给出待研究、待人工审查、已批准待执行数量与下一步阻塞类型。
- 状态读取不触发模型、研究或 Worker 工作；数据库不存在时返回 `not_initialized`，读取异常时返回 `unavailable`。
- 明确暴露 `approval_required=true`、`automatic_code_changes=false`，避免把待审档案误报为已自动升级。
- 新增回归测试：构造 researching 与 ready_for_review 两类提案，验证数量、下一步阻塞与审批边界；验证只读 SQLite 连接显式关闭，避免 Windows 临时目录清理时文件句柄泄漏。

## 验证

- 定向 `tests.test_autonomous_runtime`：17/17 通过。
- 最终全量 unittest：**1,080/1,080 通过**，约 107.389 秒，退出码 0。
- `compileall -q src tests` 同一验证 Job 通过。

## 尚未完成

本轮只补齐状态可观测性，尚未证明反馈已自动进入代码修改或能力升级。后续继续实现失败/拒绝经验回流选题、周期断点恢复与完整 Dashboard 展示。代码变更仍须满足既有用户批准、独立 Reviewer、基线/候选测试与哈希保护。

说明：`docs/PROGRESS.md` 当前含混合 LF/CRLF 行结束符，结构化编辑器拒绝对其执行事务性追加；本轮没有强制重写整份历史进度文件，先将 B-117 记录为独立阶段文档。
