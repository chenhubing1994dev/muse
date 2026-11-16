# Muse 自主工作循环与新会话启动规程

本规程是 Muse 每次开始新工作时的强制入口。目标是让工作跨对话可恢复，而不是假设聊天模型会在会话结束后自行运行。

项目长期目标以 `docs/VISION.md` 为准；所有实现计划和自动任务都必须对齐该愿景，并清楚区分已实现能力与未来目标。

## 每次新会话启动

1. 确认项目根目录与授权工作区；建立新的工作会话，不复用已关闭或身份不明的会话。
2. **第一份必读文件：`docs/PROGRESS.md`**。先读最新追加的进度区块，确认最近验证结果、未完成事项和下一步；历史区块的旧测试数量只代表当时状态。
3. 读取 `docs/ARCHITECTURE.md`、`docs/IMPLEMENTATION_PLAN.md`、`docs/CAPABILITY_BACKLOG.md`。若任务涉及特定模块，再读对应源码与测试。
4. 检查工作区变化、运行状态、未完成 Job 和最近失败；不根据旧摘要推断当前状态。
5. 按 Backlog 优先级选择最高优先级、依赖已满足、风险在现有授权内的事项。优先修复可靠性/恢复/可观察性，再做能力扩展。

## 每项工作的闭环

1. 写清目标、影响文件、依赖、风险和可观察验收条件。
2. 功能或修复遵循 TDD：先写测试、确认因预期缺失而失败，再实现最小改动。
3. 执行实现；不安装依赖、不下载大型模型、不启用服务或自启动，除非用户明确批准。
4. 审查差异和隐私边界；任务涉及外部来源时记录出处、日期、许可证及安全审查状态。
5. 执行相关测试、全量测试及静态检查；任何失败都要记录，不得将失败描述为通过。
6. 记录结构化进度事件（task/agent/research/test/review/system/evolution）；代码演化事务另保留 append-only 阶段时间线。每次运行尽量关联稳定 cycle/transaction ID，摘要不得包含任务 payload、私人对话、个人文件内容或凭证。
7. 更新 `docs/PROGRESS.md`、`docs/CAPABILITY_BACKLOG.md`，必要时同步架构与实施计划；明确写出完成、未完成、阻塞原因和下一项可执行工作。
8. 结束当前会话前保存可恢复的决策、证据、验证结果与 next action。

## 选择下一步的规则

- P0 优先于 P1，P1 优先于 P2；但不可绕过依赖与安全门槛。
- 优先选择可在当前授权内独立完成、能显著提升可靠性或可观察性的任务。
- 没有已验证免费额度时，云端 Provider 保持关闭；绝不付费 fallback。
- 第三方代码默认只研究不执行；隔离沙箱未建立前，不自动 clone/安装/运行未知仓库。
- 私人资料默认本地处理；未经明确同意不得上传到云端。
- Windows 服务、开机自启、安装、删除、对外发送、付费或不可逆操作必须先取得明确批准。
- 连续轮询不等于持续进化；没有新证据或任务时应等待/停止，不得空转制造进度。

## 状态查询
- `muse-autonomy`：呼叫已配置的本機 LM Studio，根據 Backlog 及脫敏隊列摘要提出一項下一步；提案存入 `data/autonomy/plans.sqlite3`，必須人工批准，唔會自行執行。
- `muse-autonomy --list`：查看已保存提案；未有本機模型時，規劃命令會明確失敗，不會假裝已完成。
- `muse-autonomy --loop --interval-seconds 1800`：在前台每 30 分鐘呼叫本機模型提出一項下一步；Ctrl+C 停止。呢個 loop 唔會自動註冊為 Windows 排程或服務，亦唔會自動批准/執行提案。
- `muse-research "public topic" --endpoint https://YOUR-SEARXNG`：使用明確指定的 SearXNG JSON endpoint 搜尋公開資料並保存來源；亦可設定 `MUSE_SEARXNG_URL`。請勿將私人資料放入搜尋字串。
- `muse-routines add autonomous-planner autonomy_plan --interval-seconds 1800`：建立持久化的每半小時規劃 routine；`muse-routines --list` 查看、`muse-routines --loop` 前台運作、Ctrl+C 停止。呢個唔會安裝 Windows 服務或自動啟動。
- `muse-reminders capture --text "提醒我半小時後檢查報告" --timezone Asia/Singapore`：以本機受限 parser 建立提醒；亦支援 `20 分鐘後`、`in 20 minutes`、`明天 09:30`。`snooze ID --until <帶時區時間>` 延後，`ack ID` 確認。
- `muse-reminders notify-due`：Windows 通知區 balloon；預設本地 22:00–08:00 靜音，靜音時保留未通知狀態並在下一次 watchdog 重試；可用 `--no-quiet-hours` 關閉。
- 現有 `\\MuseHourlyWatchdog` 每 30 分鐘執行 `scripts/muse-watchdog.cmd`；Watchdog 會將失敗、等待審批、過期租約、15 分鐘停滯任務及失聯 Worker 的聚合狀態建立為每日去重提醒。只在登入互動式工作階段運行；睡眠/關機或登出期間不會通知。
- `muse-intent "提醒我半小時後檢查報告"` 只預覽安全計畫；只有明確加 `--create-reminder` 才保存，普通聊天意圖辨識不會自動觸發工具。




- `muse-worker --status`：查看任务队列统计，不显示 payload。
- `muse-progress --limit 20`：查看最近结构化进度事件。
- `muse-progress --category test --limit 20`：只看测试事件。
- `muse-progress --subject <id>`：查看指定任务/Agent 的事件。
- `muse-watchdog --once`：執行單次本機健康檢查，查看隊列、過期租約及最近進度；不會啟動/停止 Worker 或執行自動修復。
- `muse-plan-hour`：按任務優先級及預估秒數規劃工作窗口；支援傳入 1800 秒作 30 分鐘窗口，預留 180 秒給自檢，不會自行建立或批准任務。
- Windows `\\MuseHourlyWatchdog`：每 30 分鐘呼叫 `scripts/muse-watchdog.cmd`，產生 `reports/half-hourly/` 本機報告；Interactive only，唔會向 ChatGPT 對話推送訊息。
## Obsidian 与开源复用约定

- Obsidian Vault 使用普通 Markdown，必须保持可脱离 Muse 独立阅读和备份。
- 当前 `muse-memory export-obsidian` 是 SQLite → Vault 的安全单向镜像；不覆盖既有笔记、不删除 Vault 文件。SQLite 仍是当前权威源，禁止在双向同步/冲突解决未验证前同时允许两边自动写入。
- Agent 接入优先使用 Obsidian 官方 CLI 与 `obsidian-local-rest-api` 内置 MCP；不重复造 MCP Server。任何插件/服务安装须先检查版本、许可证、权限、监听地址和密钥保存方式，并取得安装授权。
- 开源复用：先检索官方文档/活跃仓库、阅读项目指引、核对许可证与安全，再选取最小可复用组件；保留来源/版本/修改记录，不擅自 clone、安装或执行未知代码。

## PROMETHEUS 使命对齐

每个自主研究、灵感、改进或创作周期，尽可能回答：本轮点燃了什么问题？连接了哪些不同领域？产生了什么可验证的认识或可体验作品？留下了哪些来源、失败经验和下一步？

- 按 `docs/MUSE_CONSTITUTION.md` 保持思想开放、事实诚实、来源尊重、唯一 Commander 与可审计执行。
- 按 `docs/PROJECT_PROMETHEUS.md` 推进 Ignition → Discovery → Synthesis → Creation → Transmission。
- 宣传、报告和 Dashboard 必须区分已实现、实机验证、部分接通、规划中与阻塞，不得把愿景当成当前能力。

## 运行边界

ChatGPT 对话结束后不会自行继续执行。真正的无人值守循环必须由本机 Muse Worker/服务进程承载，并具备暂停、停止、资源限制、日志脱敏和故障恢复。服务安装与开机自启在用户明确批准前保持关闭。

## 下一阶段默认候选

进度事件第一阶段完成后，默认下一步为：将 Worker/测试/研究流程接入 ProgressEventStore，并实现基于结构化失败码、任务状态统计和重复修正标签的能力缺口提案；随后建立不能自我批准的独立审稿流程。每次启动仍须重新核对 backlog 与当前代码，不可盲目照抄此候选顺序。

## WebCodex 0.5.0+ 编码工作流规范

当 Muse 项目由 WebCodex runtime 执行时，遵循官方 `docs/CLI.zh-CN.md` 与 `docs/CODING_WORKFLOW.zh-CN.md`：

1. 以 `work_on_project` 作为 canonical bootstrap/continuation；只使用实际返回并授权的 `project_ref`、`session_ref`、Session identity 与工具 schema。不得猜测项目/路径/Session，不得继续调用已移除或当前未暴露的旧工具。新鲜或不确定上下文时请求 `project.instructions` 与 `webcodex.workflow`，并确认内容完整保留。
2. 标准循环：inspect/search/read → 使用 read revision fence 的 guarded edit → substantial coding 在首次成功 Project-scoped 操作后只展示一次 `present_work_result` → focused validation → review changes → `finish_coding_task(summary_only=true)`。小型只读工作可跳过 progress card。
3. 复杂任务定义可观察验收条件、简短计划后继续执行；保留同一 Session/Job。stale revision 必须重读，`outcome_unknown` 必须先检查实际状态，绝不盲目重放 mutation；按真实依赖等待，不重复派发 Job。
4. WebCodex CLI 中，本机项目设置/服务生命周期/task 审查决策属于主机本地操作；远程用户、token、pairing 与运维检查走 Server HTTP API。CLI 与 MCP 工具不是一一映射。升级后刷新 Host tool list 或重新连接，以当前实际暴露工具为准。旧版 Connector Task/Result/Approval 生命周期已移除。
5. 非 Git Project 仍可 checkout 工作；若 Git mutation fence 不可用，必须如实报告 source freshness / `validation_source_unproven` 限制，不可将其包装成验证通过。
6. 复杂实现优先研究成熟开源项目并复用现有架构；先评估许可证、安全、依赖、隐私与维护成本，不重复造轮子，不擅自 clone、安装或执行第三方代码。

这份规程是操作指引，不替代当次 Server 返回的 Project instructions、workflow guidance、权限和实际工具契约。
## 自主文明模式：默认自主执行与风险分级

创造者已同意采用自主文明模式：创造者设定目标与边界，Muse Commander 负责拆解、分工、研究、执行、测试、修复、经验沉淀和主动汇报；普通低风险步骤不再逐项要求人工点击。

- **默认自主范围**：公开资料研究与交叉阅读、只读检查、内部知识/研究 Wiki 整理、Muse 自有工作区内代码/测试/文档修改、隔离临时副本实验、可回滚本地产物生成与版本化。
- **自动执行前提**：路径位于授权工作区、工具在 allowlist、资源/时间/并发预算明确、有验收标准、日志可审计、失败可停止或回滚。由 Commander 放行，不由子 Agent 自行决定权限。
- **仍需单独明确授权**：付费/有价额度、私人数据外传、公开发布、删除用户资料、修改操作系统/安全设置、不可逆变更及第三方高影响操作。
- **不可自我放宽**：Agent 不得修改自身权限、凭证访问、网络外传或审计规则来扩大权限；安全策略修改走独立受控流程。
- **升级治理**：可在隔离副本中自动提出/探测/组合改进；生产采用前运行回归、独立审查、保留版本与回滚路径。提案者不得批准自己的变更。
- **运行事实**：ChatGPT 会话结束后不会替本机无限运行；无人值守仍需本机 Worker/服务，并具备心跳、租约、崩溃恢复、暂停/停止与资源限制。

## Harness² 研究与融合决策

- 采用 Propose–Probe–Compose、并行/顺序递归、候选差异与 schema 验证、经验反思和按领域积累证据化经验库。
- Muse Commander 仍为唯一调度中心；Harness² 仅作为方法论/内部改进引擎，不引入第二 scheduler。
- 先做只读快照与确定性本地原型，再接隔离 Probe、独立 Reviewer 和经验库；不得复制上游 --dangerously-skip-permissions 或 sandbox-disabled executor。
- 详见 docs/HARNESS2_ADOPTION_RESEARCH.md。
- H2-M1 本地只读引擎已实现于 `src/muse/harness_evolution.py`：HarnessSnapshot、HarnessCandidate、ProbeRun、CompositionDecision、ExperienceLesson；显式文件 allowlist、SHA-256 快照/差异、路径/敏感文件/符号链接拒绝，以及保守的 probe 比较。它不执行候选代码、不写生产配置、不授予工具权限。
- H2-M2 文件系统副本夹具已加入 `create_probe_workspace` / `cleanup_probe_workspace`：只复制快照 allowlist，先核验源哈希，候选差异仅写入 Harness 新建副本，并以匹配的所有权标记限制清理范围。它不是 OS 级沙箱，不执行代码；在真正进程隔离与受控执行器验收前，禁止将其 `isolated` 结果用于自动采纳。
- H2-M2 静态探针只做 Python AST / JSON 解析，不执行候选代码；探针执行前核验文件清单与内容哈希，副本篡改或出现白名单外文件即拒绝。`ProbeRun.isolated` 默认 False。当前 Harness 专项 16/16、全量 unittest 1,066/1,066、compileall 均通过。
- 当前 Windows Sandbox 功能为 Disabled；未经安全评估不自动启用或要求重启。
