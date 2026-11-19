# B199｜Commander 工作流验收证据回查

日期：2026-11-19

## 本轮实现

- 加强 `WorkflowController.complete()`：调用方传入 `outcome_verified=True` 不再足以完成工作流；必须回查同一 Commander Ticket 的既有事件账本。
- 必须存在由 Commander 记录、与当前绑定 Plan ID 对应且 `approved=true` 的 `commander.plan_consensus` 事件。
- `evidence_refs` 必须精确覆盖一个 `orchestration-run:<run_id>`、绑定计划中的全部 `step:<step_id>`，以及 `step:independent_review`；拒绝额外、缺失或伪造引用。
- 必须匹配该 Plan 的最新 `commander.execution_finished` 事件，且 run ID 一致、最终状态和 orchestration 状态均为 succeeded、`review_approved=true`，事件 actor 为 Commander。
- 每个绑定步骤必须存在由 Ruflo 提交的非空 `report.step_submitted` 事件。
- 每条 acceptance criterion 必须引用当前 run、独立 Reviewer 批准，以及其声明的步骤证据；非 step 前缀的自定义标准至少关联一个真实步骤。
- `workflow.completed` 事件现在持久化 `evidence_validation`：验证来源、Plan ID、run ID、逐步验证列表及 Reviewer 批准状态。
- 未新增数据库、任务队列或调度器；继续复用 Commander 的 TaskQueue 与事件账本。

## 验证

- TDD：新增“伪造 Commander 证据必须拒绝”测试，先观察到旧实现未抛出预期错误，再实现事件回查。
- WorkflowProfileTests：**23/23 passed**。
- UnifiedRuntime + TaskQueue + WorkflowProfile 定向回归：**101/101 passed**。
- 视频 Worker/Builder/Wan、旁白证据契约/编排与来源 freshness 定向回归：**98/98 passed**。
- 全项目 Python 3.11 unittest：**1,608 tests，OK，1 skipped**。
- `compileall`：`src/muse` 与 `tests` 通过。
- 最终格式清理后重新执行 WorkflowProfileTests：**23/23 passed**。

## 信任边界与未完成

- 本轮验证的是同一 Ticket 内 Commander/Ruflo 事件链的一致性，不是任意文件内容或外部系统状态的自动核验。
- TaskQueue 事件账本仍是本地应用信任边界，不是防篡改的密码学审计日志；拥有本地数据库写权限的代码仍可能伪造事件。
- 下一步可为本地交付物增加受限的路径 + SHA-256 证据解析器，并为外部系统定义明确的只读验证适配器；不能把普通 URL/字符串引用直接当作已验证内容。
- 本轮没有触发新的 GPU 生成、外部发布或对外不可逆动作。
