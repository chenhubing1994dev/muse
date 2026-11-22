# B343 — 研究报告到商业试点候选桥接

更新时间：2026-11-22

## 状态

新增的独立桥接层已完成，专项目标测试 **6/6 通过**。本轮没有修改已有 Inspiration、Research Wiki、InitiativeStore、Commander、Video Worker 或 Status API 模块。

这是一段可被现有流程调用的明确接口；**尚未自动接入**现有的 evolve_inspiration_closed_loop。相关上游文件正在其他活跃 Session 中审阅，为遵守单写者边界，本轮不直接改写那些文件。

## 新增文件

- `src/muse/research_business_bridge.py`
- `tests/test_research_business_bridge.py`

主要入口：

`create_candidate_from_research_report(report, business_brief, ratings, business_database=..., report_path=...)`

调用方必须明确提供六项商业简报字段：标题、客户细分、客户问题、拟议方案、验证实验、成功信号，以及 BusinessOpportunityStore 定义的六项商业评分。价格和成本估算可不提供；如提供，仍标记为未经验证的调用方估算。

## 关键行为与边界

1. 复用既有 ValueForge、BusinessOpportunityStore，不另建评分器、商机库或调度器。
2. 研究 URL 先经过 http/https、主机名及 URL 凭证过滤，再进入 ValueForge；报告内容只用于假设评估和来源追溯。
3. 研究来源只追加为 `estimate / assumption` 类型记录，不冒充客户访谈、市场数据或付费意愿证据。
4. 同一商业方案使用稳定幂等键；新研究周期复用同一商机并追加一条新的假设记录；相同报告重放不会重复追加。
5. rejected / parked 商机不接受新研究写入，避免被静默重启。
6. 不自动创建或晋升 Initiative，不创建 Commander Ticket，不启动 Worker、视频生成、GPU、发布或外部发送。
7. 商业评分只是既有启发式评分，不是收入预测、转化概率或市场验证结果。

## 验证

通过 Muse 正式 psmux 工作流运行：

- `tests/test_research_business_bridge.py`：**6 tests / OK**，最终复验 Job `2783d8180a414114a00ff77460572724`。
- 已覆盖候选创建、研究仅作假设、来源 URL 净化、精确重放幂等、新研究周期复用同一商机并追加记录、拒绝状态保护、输入校验。
- 未运行全仓回归。
- Runner 原生 pytest 验证尝试因共享源码快照仍有并行写入（quiescent=false），未能生成完整测试摘要或测试计数；这次尝试不计作通过或失败。
- 当前项目工作区不是 Git checkout，Git diff / clean 状态无法验证；卫生检查未发现阻断性临时文件或敏感路径问题。

兼容性检查另发现当前共享快照中 `tests/test_value_forge.py` 为 **13 tests / 1 failure**：`test_capability_radar_adapter_ignores_terminal_proposals`。当前 `evaluate_capability_radar_result` 未排除 rejected / implemented / rolled_back / approved 状态，并在第二轮循环复用了上一轮的 proposal_id，测试得到五条重复的 open-1。该问题不在本轮新增文件内；已通过 Session 协作消息通知 ValueForge 原工作线，本轮不越权修改其文件。

## 下一步接线条件

待当前 Inspiration / Research / Initiative 并行工作线确认文件归属后，增加一个明确的人机交互入口：用户或上游任务提交商业简报及评分，再调用本桥接层。随后仍由既有商机账本、Initiative 审核和 Commander 审批完成后续工作。不可从单份研究报告自动推导客户需求或自动派发。
