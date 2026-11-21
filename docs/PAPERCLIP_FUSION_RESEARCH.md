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

- B239（已完成首阶段）：Agent 职位/汇报树/组织变更审计，旧数据库增量迁移。
- B240（已完成首阶段）：现有 `/api/v1/agents` 增加最多 500 节点的只读组织快照（edges/total/truncated/unresolved_parent_count）；现有 Svelte AgentGalaxyPanel 展示职位、汇报关系、状态、能力与截断/未知上级提示。
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

## B241 前置审计：真实用量与归属链路

- B241 初次审计时，`LMStudioClient.chat()` 只返回 `choices[0].message.content`，丢弃响应中的 `usage` 与实际模型元数据；B264 已补 `chat_with_usage()` 与 `LMStudioAdapter` 透传，但 Muse 的 `worker`、`autonomous_research` 等主路径仍存在直接客户端调用。
- `OpenRouterFreeAdapter` 只提取经过类型校验的 `prompt_tokens` / `completion_tokens` / `total_tokens`；`ProviderMetricsStore` 目前只持久化 provider 成功/失败、EWMA latency、错误类型和熔断时间，不持久化用量或成本。
- `ProviderRegistry` 当前主要存在于适配器/策略测试与 OpenRouter 适配路径，未发现它已成为 Muse 主流程统一模型网关；`ChatRequest` 也没有 task/agent/project attribution context。因此不能把 ProviderRegistry 的健康指标冒充 Agent 实际用量。
- B241 的正确前置顺序：先统一模型调用上下文与观测出口，再建立 append-only usage ledger；每条记录至少绑定 provider/model、observed_at、usage_source、input/output/total tokens，并允许 task_id/agent_id/project_id 暂缺但明确标记未归属。实际费用必须带 provider 返回的 cost/currency，或可追溯的价格表版本；估算与实测分开存储。
- 只有在调用归属、计量来源、周期边界与并发预留/结算都可验证后，才启用 Agent/项目金额 hard-stop。未知成本不得当作 0；不能只靠 workload 秒数或 free request quota 推算美元费用。

## B241 实现进度：用量账本首阶段（B264）

- 已实现 append-only `UsageLedger`、稳定 `event_id` 幂等、精确十进制金额、币种/成本来源与估算 `price_version`，并通过可选 `UsageRecordingAdapter` 将 provider-reported token usage 写入账本。
- LM Studio 的 OpenAI-compatible 响应现通过 `chat_with_usage()` 安全提取 `prompt_tokens` / `completion_tokens` / `total_tokens` 和实际模型名；旧 `chat()` 字符串接口保持兼容。`LMStudioAdapter` 透传用量，OpenRouter 原有 token usage 可通过同一 observer 记录。
- B266 已为 `worker.run_once()` 的 handler 执行线程加入 task-scoped `ModelCallContext`，并让 `LMStudioClient.chat_with_usage()` 自动记录当前作用域的 token usage；这不是全局上下文，Worker 外直接调用、Commander DAG 线程及新建子线程仍未覆盖。adapter attribution 仍为构造期绑定，缺失项明确标记 partial/unattributed。真实费用未观测到时保持 NULL，估算必须带 price_version。
- 定向验证 29/29 passed（Python 3.11 / psmux Job `a5a1ed5d68594b2db7c862a96ae063e6`）。金额预算 hard-stop、价格目录、周期预留/结算和全主流程接线仍未实现。

## B266 实现进度：动态调用归属首个执行切片

- B266 新增 ContextVar 驱动的 `ModelCallContext`，由 `worker.run_once()` 在实际 task handler 执行期间安装；`LMStudioClient.chat_with_usage()` 自动把 provider-reported usage 追加到任务账本。没有 task scope 时不产生隐式全局账单。
- 当前 project_id 使用 `muse`；agent_id 仍为空，因为 TaskQueue 的 worker_id 是临时执行租约身份，不等同于 AgentRegistry 的稳定 Agent 身份。账本正确标记 partial attribution，不伪造 Agent 归属。
- UsageLedger 在模型调用首次发生时 lazy-open，避免纯非模型任务创建空数据库。上下文退出时关闭账本并 reset ContextVar。
- 定向验证 52/52 passed：上下文 4、Worker 34、Model Client 5、Provider 9。覆盖的是 Worker 同步执行线程内的 LM Studio 调用，不自动覆盖 handler 新建线程/进程、Worker 外直接调用、Commander DAG 线程或 OpenRouter。
- 仍需实现：跨线程/异步任务上下文传播策略、真实 AgentRegistry attribution、统一 Provider gateway、OpenRouter 自动观测、真实费用/版本化价格目录、预算原子预留与结算。任何金额 hard-stop 必须等这些证据链完整后再启用。

## B270 实现进度：模型用量事件显式幂等键

- LMStudioClient.chat() / chat_with_usage() 增加可选 usage_event_id。调用方可为同一个逻辑模型请求在重试时复用该键；本地 observer 将它映射到 UsageLedger event_id 和 request_id，不传给 provider。
- 同键、相同 usage payload 幂等返回原记录；同键、不同 payload 冲突，记录失败但不向上抛出成推理失败。未提供键仍使用随机 ID，不保证跨重试去重；不使用 task_id 冒充调用级 ID。
- Python 3.11 定向回归 54/54 passed：ModelCallContext 6、Worker 34、Model Client 5、Provider 9。Runner Job wc_job_jSEItZs2DFqE21F8 exit 0。
- 仍待：稳定 AgentRegistry 身份注入、非 Worker 直接调用覆盖、跨线程/进程传播、OpenRouter 自动观测、真实价格目录与预算原子预留/结算。

## B272 实现进度：AgentRegistry 任务绑定与模型用量归属

- `worker.run_once()` 支持显式 Agent 归属参数，但只有 `agent_id` 与 AgentRegistry 数据库同时提供、且指定 task_id 时才启用。Worker 在 claim 前验证 Agent 已登记、enabled=true、`current_task_id == task_id`；不匹配即拒绝，队列任务不被领取。
- 验证通过后把稳定 AgentRegistry.agent_id 注入 ModelCallContext，账本可将 token usage 归属该 Agent。未提供绑定仍为 partial attribution；绝不从 worker_id、payload 或 role 猜身份。
- 定向验证 42/42 passed：ModelCallContext 8、Worker 34。覆盖正向身份归属和负向错误绑定 fail-closed。
- 目前仅建立 Worker 接入 seam，生产调度器尚未全部写入/维护 AgentRegistry current_task_id；Commander DAG/Ruflo 子 Agent 需各自完成真实身份登记和任务绑定。其他 Provider、跨线程/进程传播、价格目录和预算仍待实现。

## B275 实现进度：Commander Ruflo AgentRegistry 生命周期接线

- MuseRuntime 复用既有 `data/agents/muse.sqlite3`。Commander 从 Ruflo 收到真实 `agentId` 后登记为 `source="ruflo"`；执行期间 heartbeat 写入当前 task，执行完成/失败时由 `finally` 释放 busy 状态。
- 单 Agent dispatch、Plan Critic/格式修复、DAG 子 Agent/空结果替换 Agent、Reviewer 格式重试均使用统一 `_agent_execution_scope()`，并在同步执行期间安装带稳定 agent_id 的 ModelCallContext。
- 验证 89/89 passed：ModelCallContext 8、Worker 34、UnifiedRuntime 47；新增 Commander 集成回归检查 Agent 登记和结束后的任务解绑。
- 尚未证明 Ruflo MCP `agent_execute` 响应含可信 token usage；本轮不记录估算 token 或费用。Ruflo provider 用量需接入可信 usage 字段/底层 provider telemetry；跨线程/进程传播也未完成。

## B283 实现进度：Ruflo Provider 用量进入 Muse UsageLedger

- 本机 Ruflo 安装版本 `@claude-flow/cli` **3.71.0**。`agent-tools.js` 的 `agent_execute` handler 将 `executeAgentTask()` 结果直接返回；底层 Anthropic 与 Ollama/OpenAI-compatible caller 均构造 `model`、`messageId` 与 `usage` 字段，因此这不是从 Agent 自然语言输出中猜测 Token。
- Muse `MuseRuntime._record_ruflo_usage()` 在六类 `agent_execute` 调用仍处于 `_agent_execution_scope()` 时运行，复用当前 `ModelCallContext` 写入现有 `model_usage.sqlite3`；provider_id 按实际 Muse 配置/路由归属（包括 ChatAnywhere），model 来自 Ruflo Provider 响应，Agent/Task/Project 归属由 B275 scope 提供。
- 只写 Provider 原始 `inputTokens` / `outputTokens`。Anthropic 的 `totalTokens` 是 Ruflo 用两者合成，且 response 没有完整保留 prompt-cache token 拆分；OpenAI-compatible caller 对缺失 usage 字段会填 0。Muse 因此要求输入、输出均为正整数，并拒绝缺失、布尔、负数和零值回退；`total_tokens` 保持 NULL，避免把合成值误标为 Provider 原始总量。
- 只有真实 Provider `messageId` 才构造 `ruflo:<messageId>` 稳定 event_id；Ruflo 生成的 `ollama-*` 时间戳 ID 被排除。没有稳定 ID 时使用账本随机事件 ID，不保证跨重试幂等。
- 回归：UnifiedRuntime 51/51、ModelCallContext 10/10、RufloAdapter 22/22、UsageLedger 11/11 passed，另验证 ChatAnywhere provider 身份归属。Worker 35/36；唯一失败在 Contextual Inquiry review-only 加密案件 Ticket workflow contract 校验，属于本次未改动的独立路径，待对应并行工作流核查。
- 边界：本轮未发起真实模型推理请求；没有记录真实费用、推算费用或启用预算 hard-stop。跨线程/进程传播及 Ruflo 之外的 Provider 主路径仍需单独接通。

## B293 实现进度：Commander 本地规划模型用量归属

- LocalModelCommanderPlanner 使用 LMStudioClient.chat()；该接口最终经过 chat_with_usage()，可将本地 Provider 返回的 token usage 写入当前 ModelCallContext。
- dispatch_commander_once() 的 initial_plan 生成现在运行在 model_call_scope(task_id, project_id="muse") 中，写入同一 model_usage.sqlite3。
- 规划器是系统级规划调用，不伪造 AgentRegistry 身份；该类记录 agent_id=NULL / partial attribution。Ruflo Agent 的 usage 仍由 B275/B283 的 _agent_execution_scope() 和真实 Provider 响应归属。
- Python 3.11 定向验证 97/97 passed：UnifiedRuntime 54、ModelCallContext 10、RufloAdapter 22、UsageLedger 11；新增测试确认 planner Token 按真实 task/project 记录、无虚构 agent_id，且 ContextVar 在退出后 reset。
- 未完成边界：尚未验证真实本地模型推理端到端账本记录；费用价格表、实际费用、预算预留/hard-stop、跨线程/进程传播和 Ruflo 之外的其他 Provider 主路径仍未全面接通。
## B295 实现进度：自主研究 Routine 用量归属

- AutonomousSupervisor._autonomous_research() 由 RoutineStore 调用，不经过普通 Worker 的 TaskQueue ModelCallContext；其本地研究合成、主张审查与反馈审查模型调用原先可能漏记。
- 在 engine.run_once() 外围安装 model_call_scope。Routine 没有 TaskQueue Ticket 时只记录 Muse project 级用量（task_id=NULL、agent_id=NULL、partial attribution），不伪造任务或 Agent 身份；若已有外层 ModelCallContext，则保留真实 Task/Agent 归属。
- AutonomousRuntime 定向测试 27/27 passed，新增测试覆盖 partial 项目归属、外层上下文保留与 ContextVar reset。
- 未完成边界：尚未验证真实模型请求；inspiration_evolution 直接调用 AutonomousResearchEngine 的路径、其他无 TaskQueue 的模型调用、跨线程/进程传播、价格表与预算 hard-stop 仍待接通。
## B297 实现进度：AutonomousResearchEngine 统一模型用量边界

- 将 scope 放到 AutonomousResearchEngine.run_once() 公共入口：无外层 ModelCallContext 时建立 Muse project partial attribution；有外层 Task/Agent 上下文时保持原归属。
- 覆盖 RoutineStore 自主研究与 inspiration_evolution 直接调用等多条研究入口，避免只在单个调用方修补；与 B295 Routine scope 叠加时由引擎检测并复用当前上下文，不重复入账。
- 定向回归 86/86 passed：AutonomousResearch 59、AutonomousRuntime 27；验证 project-only partial 记录、外层 task/agent 保留、ContextVar reset。未执行真实 Provider 推理或成本测量。
- 未完成边界：其他不经过 AutonomousResearchEngine 的本地模型入口（Gemma 意图、助手对话、记忆抽取、CLI autonomy/improvement 等）、跨线程/进程传播、真实费用价格表和预算 hard-stop 仍待覆盖。
## B298 实现进度：Gemma 意图分类线程上下文传播

- LMStudioGemmaIntentClassifier 将 client.chat() 放在超时保护线程中，原先不会自动继承调用方的 ModelCallContext，导致 Worker/任务内分类请求可能失去 Task/Agent usage 归属。
- 使用 contextvars.copy_context() 捕获调用线程上下文，并在后台线程内通过 Context.run(work) 执行；保留已有真实归属，不生成虚构 Agent/Task，不改变分类和审批行为。
- GemmaIntent 定向回归 10/10 passed，新增测试确认后台线程能把 Token usage 记到原 Task/Agent；未执行真实模型推理或成本测量。
- 独立 CLI 预分类在没有外层上下文时仍不自动写 project-only usage；助手对话、记忆抽取、其他无任务入口及跨进程传播仍待覆盖。

## B299 实现进度：Muse CLI 意图分类项目级归属

- `chatgpt_bridge` 的 `classify`、`route`、`submit` 入口现在在没有既有 ModelCallContext 时建立 Muse project partial scope；submit 以实际 TaskQueue 数据库路径派生 UsageLedger 路径。
- 已有 Task/Agent scope 原样保留；B298 的后台线程 ContextVar 传播让 CLI 入口 scope 延续至 LM Studio Gemma 请求。
- 新增测试覆盖 project_id、空 task_id/agent_id 与 Provider-reported input/output token；Python 3.11 定向测试：ChatGPT Bridge 9/9、Gemma Intent 10/10 passed，compileall 通过。未进行真实模型推理或费用测量。

## B303 实现进度：助手对话与记忆抽取用量归属

- `chat_cli.run_turn()` 为独立助手回复与 opt-in memory extraction 提供统一 Muse project partial scope；默认 UsageLedger 路径与中心 TaskQueue 数据库同目录。
- 有现存 ModelCallContext 时不覆盖真实 Task/Agent 归属。助手聊天和候选抽取共享一次 turn scope，但各自 Provider 调用分别形成 usage 事件；记忆候选仍需用户审核。
- 定向验证：Chat CLI 4/4、Assistant 4/4、Cantonese TTS 8/8 passed；相关 compileall 通过。未执行真实模型推理或费用测量。

## B304 实现进度：ChatGPT Bridge 请求幂等与重放保护

- `submit --request-id` 增加边界校验，并将稳定 ID 绑定到完整不可变任务请求信封；相同 ID 的重复请求返回原 Ticket，不重复创建任务或事件。
- 请求身份比较忽略队列后续添加的审批元数据与 advisory intent 分类结果，因此即使本地 Gemma/规则分类在重试间发生变化，也不会把同一请求误判为冲突；响应继续报告 Ticket 中保存的原始分类来源。
- 相同 ID 被用于不同请求、标题、优先级或耗时预算时，返回 `request_id_conflict`，不覆盖原 Ticket、不执行；审批后重放仍幂等。无效 request_id 在分类器调用和队列写入前拒绝。
- Python 3.11 / psmux 验证：Bridge 12/12、Chat CLI 4/4、隐私边界 2/2、Cantonese TTS 8/8，合计 26/26 passed；Bridge/CLI `py_compile` 通过。未进行真实模型推理或费用测量；无自动审批/派发。未进行真实模型推理或费用测量；无自动审批/派发。

## B319 后续复验：Chat 入口用量归属与单写者边界

- 当前共享快照重新执行 ChatGPT Bridge 12/12、Chat CLI 4/4、Gemma Intent 10/10、Assistant 4/4，合计 30/30 passed；8 个相关源码/测试文件 compileall 通过（psmux Job `c870430717a6411f854dddeb042c52fd` exit 0）。
- `chatgpt_bridge` 的 classify/route/submit 与 `chat_cli.run_turn()` 只在无外层 ModelCallContext 时建立 project-level partial attribution；已有真实 Task/Agent 上下文不被覆盖。Gemma 超时线程通过 `copy_context()` 保留调用方上下文。
- Worker/Builder/TTS/VideoStudio/Commander admission 不属于 Paperclip Session 当前写入范围；Worker + tests 已正式交给主视频 Session 单写者维护。本 Session 只做只读审查和非冲突 Chat/usage 维护。
- 未执行真实 Provider 推理或费用测量；unknown token/cost 仍保持 unknown，不启用预算 hard-stop。






