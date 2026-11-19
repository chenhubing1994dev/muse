# 2026-11-19｜B-063 源码审查与拒绝反馈研究 Ticket

## 真实源码审查

- 当前工作区源码差异已通过真实本地 Gemma Reviewer 调用 `IndependentReviewGate.review()`；ReviewStore 临时库写入后读回成功。
- 审查记录：首轮 `ecc33bb2e31f47d58f296801bff2f90b`；本轮拒绝反馈源码复审 `b3add74f52b64c87b14270aa99bb173c`；Reviewer ID：`local-reviewer:011d7c5831f7d581e81cce35`。
- 首轮审查输入为 `autonomous_runtime.py` 与 `test_review_gate.py` 的实际 Git diff；前者同时含既有 `video_pipeline` 接线改动，因此 Reviewer 也看到了非 B-063 hunk。本次不代表审查整个 workspace。
- Reviewer 返回 `approved=true`，并列出 info、low、info、medium 四条 finding；没有 critical/high finding。Gate 的既定策略只对 critical/high 自动否决，所以最终记录为通过。medium finding 是测试覆盖建议，不等于没有问题，也不构成代码变更批准。
- 第二轮针对拒绝反馈/Worker handoff 核心源码块的真实审查：`approved=true`，1 条 info finding；ReviewStore 写入并读回成功。首次较长输入的模型输出格式无效，Gate fail-closed；缩短到核心源码块后复审通过。
- 真实模型拒绝 smoke：Gemma 对合成 SQL 注入样本给出 `approved=false`，列出 critical 与 high finding；ReviewStore 写入并读回成功（Review ID `429392b8130a4590b733dd7469254767`）。仅为合成安全样本，不代表 Muse 生产拒绝链路已实测。

## 拒绝 → 研究 Ticket

- `src/muse/evolution_executor.py`：当真实审查结果为 `reviewed_rejected` 时，创建 `kind=review_followup_research` 的持久化 TaskQueue Ticket。
- Ticket 绑定 review ID、review node、artifact、proposal、feedback 与 plan ID；Reviewer 摘要/发现明确标记为不可信数据。
- Ticket 的任务范围为 `read_only_research`：调查每条 finding、搜集支持与反面证据、提出有界修复方案；禁止编辑文件、执行命令或批准代码变更。任何实现仍必须走单独的人类批准 code-change Ticket。
- 使用 `review-followup:<review_id>` 幂等键。相同 run 重放复用原 Ticket，不重复排队或重复追加 `review.followup.requested` 事件；approved review 不创建拒绝跟进 Ticket。
- 拒绝决策节点写入 proposal/feedback ID 并标记 `rejected`；`AutonomousResearchEngine._next_topic()` 接受两种独立审查决策类型，生成 `rejected_review_followup` 动态研究主题。公开检索使用通用支持/反面查询，不泄露 Reviewer 私有 findings；已研究 query pair 自动降优先级。
- `review_followup_research` 已注册 Worker handoff：Worker 只返回 `handoff_registered` 和 `preferred_topic_key`，明确 `research_completed=false`、`code_changes_performed=false`；Commander 本地 Worker 仅认领该 allowlisted kind，实际研究由 autonomous_research routine 承接。

## 验证

- B-063 focused regression（ReviewGate、拒绝 Ticket 幂等、approved 不创建 Ticket、新决策类型进入动态研究队列）：**18/18 通过**。
- Worker handoff 专项：`test_worker.py` **32/32 通过**；Commander 运行时：`test_autonomous_runtime.py` **19/19 通过**。
- `compileall -q src tests` 通过。
- 最后一次全仓绿灯快照：**1,563 项运行，1,554 通过、9 项跳过、0 failures / 0 errors**（约 165 秒）；当时 `compileall -q src tests` 通过。

- 注意：全仓绿灯后，另一个仍在运行的并行任务改动了 `src/muse/initiative_engine.py`，当前文件在约第 521 行出现重复参数/括号 SyntaxError。该任务仍运行中，本轮未覆盖其文件；B-063 涉及模块的 `py_compile` 已通过，但当前全仓状态需等该并行改动结束后复验。
## 下一步

1. 通过 `run_feedback_research_experiment` 使用真实模型触发拒绝，验证生产 ReviewStore/TaskQueue 与动态研究主题的端到端路径。
2. 实际运行一次 `rejected_review_followup` 研究周期，核对支持/反证来源、研究结果入库与 query-pair 去重。
3. 恢复 Builder `1235` 并核实 Provider/进程身份；在确认前保留 `provider_unverified` 标记。

所有变更均保留现有工作区内容；未清理、重置或提交其他任务的文件。

## 2026-11-19｜修复后实测与拒绝反馈闭环验收更新

- 并行语法问题已由原写入会话修复；复查 research_knowledge.py 对应代码后，Python 3.11 compileall -q src/muse 通过，test_feedback_experiments.py 56/56 passed。
- 修复后全仓 Python 3.11 unittest：1,595 tests，163.586s，OK，1 skipped，0 failures / 0 errors。唯一 skip 是当前解释器未安装可选依赖 celestial-calendar==0.8.0。测试输出仍出现一次 FFmpeg moov atom not found 警告，未定位到具体媒体文件；不影响测试退出码，但媒体完整性问题仍待单独定位。
- 正向真实模型 E2E：真实 Gemma Reviewer 审查 run_feedback_research_experiment 实际运行的 56 项 allowlisted 回归，返回 reviewed_approved；Review ID 33e1767d736949cfaf585761f5f561b3。临时 SQLite 存储，approved 不创建 follow-up Ticket。
- 负向真实模型 E2E：只在临时 workspace 副本的 test_autonomous_research.py 追加一个受控失败用例；allowlisted suite 实际运行 57 项、1 项失败。真实 Gemma 返回 reviewed_rejected / approved=false，ReviewStore 读回成功、1 条 finding；TaskQueue 实际生成且只生成 1 条 review_followup_research，状态 queued、scope read_only_research，事件为 ticket.created 与 review.followup.requested。失败 fixture 未写入正式源码。
- 拒绝研究周期 E2E：在独立临时知识库写入拒绝决策，使用受控 Search/Reader 适配器运行真实 AutonomousResearchEngine.run_once()；周期 completed，topic origin 为 rejected_review_followup，检索并读取 2 个来源，分别以 support_search_candidate 与 counter_search_candidate 写入 Claim evidence。Claim 仍为 hypothesis_only_not_fact；下一轮 topic selection 不再重复已研究 query pair。
- 所有上述端到端数据库均为临时目录；未触碰生产知识库、TaskQueue 或 ReviewStore。same-endpoint 的不同模型只证明模型身份区分，不证明 Provider/进程独立性。
- 当前 Builder 配置仍指向 127.0.0.1:1235/v1（不可达）；Reviewer Gemma 位于 127.0.0.1:1234/v1（可达）。Provider-level independence 与 Builder 恢复仍 pending。

## 下一步

1. 恢复并核验 Builder 1235 端点及进程身份，再做不同 Provider 的独立审查验证。
2. 定位全仓测试期间的 moov atom not found 媒体文件，不删除或覆盖未知素材。
3. 让只读研究 handoff 在 Commander/AutonomousResearch Routine 的真实常驻运行中持续消费，并补充运行状态与恢复观测。
