# Paperclip 万象超融合研究与 Muse 集成设计

更新时间：2026-11-21
上游：<https://github.com/paperclipai/paperclip>
许可证：MIT（以本次检查的上游 LICENSE 为准；复用源码时保留版权与许可证声明）。

## 结论

Paperclip 最有价值的定位是 AI 组织的 control plane：把组织目标、员工/Agent、汇报关系、任务、预算、审批、连接权限、运行记录和产物放进同一个治理视图。它不是 Agent 模型或执行框架；执行由外部 harness/adapter 完成。Muse 应吸收其控制面设计，而不是引入第二个调度中心。

**架构裁决：Muse Commander 仍是唯一调度权威。** Paperclip 的概念映射到 Muse 既有组件与同一 SQLite 任务账本；不得另建 Paperclip 服务、第二任务队列或并行 scheduler，除非未来用户明确要求独立部署实验。

## 上游机制与 Muse 映射

| Paperclip 机制 | Muse 现有基础 | 融合策略 |
|---|---|---|
| Organization goal / goal ancestry | CommanderPlanner、TaskQueue、workflow bindings、Initiative/Goal 关联 | 每个 Ticket/Agent step 保留目标祖先引用；不把自由文本目标当作授权 |
| Org chart / title / reports-to | AgentRegistry、AgentRoleRegistry、SpecialistRouter | 在既有 AgentRegistry 增加单上级汇报关系、职位与变更审计；禁止环与未知上级 |
| Agent adapters | SpecialistRouter、Ruflo adapter、LocalHTTP adapter、MCP/CLI 适配层 | 统一适配器描述/能力/健康状态；只发现已配置服务，不自动运行未知程序 |
| Atomic checkout / single assignee | TaskQueue.claim_next 的 BEGIN IMMEDIATE、lease_owner、lease expiry | 保留同一队列与租约语义；未来补充显式 assignee/assignment event，而非重复造队列 |
| Heartbeat / wakeup | AgentRegistry.heartbeat、TaskQueue.heartbeat、Routines、Watchdog | 统一显示在线/忙碌/离线/阻塞；心跳只报告状态，不授予额外权限 |
| RBAC / tool connection rules | AgentRoleRegistry capability allowlist、Worker handler registry、approval gates | 采用默认拒绝、按 Agent/任务/工具细分；密钥与执行凭据不得进入普通 Agent 配置或审计正文 |
| Budget / spend / hard stop | FreeOnlyRouter 的配额 fail-closed、ProviderBudget、任务 workload estimate | 先接真实 provider usage/cost 证据；未知成本不得估成零或伪造金额。未有可靠计量前不启用虚构美元预算 |
| Board approvals / pause / rollback | TaskQueue waiting_approval、human.decision、WorkflowController、ImprovementStore | 保留每任务显式审批；审批是指定范围、指定版本的决策，不自动扩展到其他工具或任务 |
| Audit / artifacts / diffs | task_events、ProgressEventStore、OrchestrationStore、ReviewStore、Video Attempt/Manifest | 使用既有事件账本和产物哈希/证据链，避免第二套审计日志 |
| Skills / evaluations | Skills runtime、prompt_engine、IndependentReviewGate、test runner | 将 Agent 技能版本与 eval 结果挂到角色/任务；升级仍需审查、测试与回滚 |

## 本轮实现（B239 第一阶段）

- 在 `AgentRegistry` 的既有 `agents` 表中向后兼容增加 `title` 与 `reports_to` 字段；旧数据库启动时自动迁移，不重建或清空原记录。
- 增加 `set_org_assignment()`：必须引用已登记的经理；同一 Agent 只有一个直接上级；拒绝自我汇报、未知经理和任何传递环；每次组织变更以 `org.assignment_changed` 写入同库审计表。
- 增加有界 `org_chart()` 节点/边快照和 `list_org_events()`；超出读取上限时显式返回 `truncated`，不伪称完整组织图。
- Status API 的 Agent 摘要增加 `title`、`reports_to`，为前端组织图提供数据字段。

## 重要边界与未完成项

1. **预算**：目前 Muse 有 provider 免费配额门禁和任务耗时估算，但并无完整、可信的逐 Agent 实际 token/费用计量。下一步先统一采集 provider/model/usage/currency/source/timestamp，并区分估算与实测；只有实测数据可累计真实费用。达到 hard stop 后必须在任务认领/执行前阻断，并记录预算事件。
2. **权限**：现有角色能力白名单不是操作系统沙箱。`restricted/standard/privileged` 是应用层 admission policy，不可宣称隔离任意 Python callable、shell 或文件系统。
3. **审批**：现有 CLI approve 仅记录决定并将 Ticket 放回队列；真实执行仍要求 `dispatch_commander_once(..., approved=True, task_id=...)`。不得把一次审批解释为永久授权。
4. **分派**：目前 DAG 子步骤由 Commander 按角色/能力/Agent 健康和效果指标路由；还没有通用的持久化单 assignee 组织任务委派 API。后续应把 Mandate/Assignment/Report 事件接入原 TaskQueue `task_events`，不另建 Ticket 表。
5. **审计**：组织关系变更已单独追加审计；任务运行、工具调用、diff、截图和成本应继续引用既有账本/产物系统，避免把敏感 prompt、secret、用户私人资料写入事件。
6. **移动端/看板**：先在现有 Svelte 桌面指挥中心增量呈现组织树、Agent 状态、任务、审批与成本；不复制 Paperclip React UI，也不宣称手机端适配完成。

## 建议实施顺序

- B239：Agent 职位/汇报树/组织变更审计（本轮）。
- B240：组织树只读 API 与现有 Agent 看板可视化；显示不完整状态和未知父节点。
- B241：Agent/任务/项目预算计量契约；真实 usage 证据、估算/实测分层、月度窗口和原子 hard-stop。
- B242：统一审批/权限矩阵：人类、Agent、工具连接三层身份与操作权限；每次授权限定资源、动作、时效。
- B243：Mandate → Assignment → Report 生命周期映射到现有 TaskQueue 和 workflow events，单认领与阻塞升级。
- B244：Adapter 心跳与执行 trace 统一视图；运行恢复必须使用原 Job/session identity，禁止重复派发未知结果。
- B245：Agent 训练/evals/技能版本和绩效反馈，改进需 review/test/rollback。
## 参考资料

- Paperclip README：<https://github.com/paperclipai/paperclip/blob/master/README.md>
- Core concepts：<https://github.com/paperclipai/paperclip/blob/master/docs/start/core-concepts.md>
- Architecture：<https://github.com/paperclipai/paperclip/blob/master/docs/start/architecture.md>
- Org structure：<https://github.com/paperclipai/paperclip/blob/master/docs/guides/board-operator/org-structure.md>
- License：<https://github.com/paperclipai/paperclip/blob/master/LICENSE>

上游文档与实现会持续变化；后续吸收必须针对当前 commit/文件重新核对，不能仅凭宣传页描述推断能力已在 Muse 实现。
