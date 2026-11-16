# B-118：拒绝审查经验回流与 Evolution Cycle 断点恢复

日期：2026-11-13

## 本轮完成

### 1. 独立审查拒绝经验回流研究选题

- `AutonomousResearchEngine._next_topic()` 现在读取 Research Wiki 中 `decision` 类型、状态为 `rejected` 的节点。
- 只接受 `decision_kind=improvement_validation_review`、`approved=false` 且具备 proposal ID 的工程审查拒绝节点。
- 为每个拒绝决策建立稳定主题键 `rejected-review:<decision-node-id>`，后续研究结果沿既有 idea/experiment/claim 与 Evolution Cycle 账本保存。
- 外部搜索只使用通用的根因分析、独立代码审查、回归测试、反例与替代解释查询；拒绝 findings、私人反馈正文、reviewer 和 feedback ID 不进入搜索查询。
- 拒绝审查经验只作为工程研究信号，不被当作外部事实证据，也不自动批准或升级能力。

### 2. Evolution Cycle 中断恢复

- 新增 `_interrupted_cycle_checkpoint()`：读取最近 200 条 evolution 事件，识别最新未完成周期；终态 completed/failed/cancelled 不会被恢复。
- 每个周期选定研究主题后，以有限长度的 `topic_key:` progress 事件保存检查点。
- 进程中断后复用原 cycle ID，并优先恢复原主题；若主题已不存在，则回退到当前选题器的安全选择。
- 恢复操作会追加 progress 事件，保留完整审计轨迹；完成后写入 terminal event，下一次调用不会复用已完成周期。
- 周期恢复不跳过研究、测试、独立审查、用户批准或哈希保护；重跑依赖现有持久化节点和执行器的幂等约束。

## 验证

- 新增拒绝审查回流与隐私隔离测试，先确认旧代码失败，再完成实现。
- 新增中断周期 ID 与 topic checkpoint 恢复测试，并验证终态周期不会再次恢复。
- `tests/test_autonomous_research.py`：49/49 通过（本轮最后一次模块回归）。
- 本轮完整回归：`python -m unittest discover -s tests -q`，**1,082 tests passed**，约 107.487 秒，退出码 0。
- `python -m compileall -q src tests` 通过。

## 当前边界

- 这次补齐的是拒绝审查经验回流与周期级断点恢复；尚未证明整个生产环境长期无人值守运行数小时/数天并稳定恢复。
- 研究周期恢复依赖进度账本最近 200 条事件；超出保留窗口的极旧未完成周期不会被自动猜测恢复。
- 代码候选仍必须经过显式批准、独立 Reviewer、baseline/candidate tests 与 hash guard。
- Dashboard 对 B-117/B-118 的展示尚未完成；当前成果先通过后端状态与 Research Wiki/进度事件持久化。
