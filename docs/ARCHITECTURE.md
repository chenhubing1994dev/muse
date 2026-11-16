# Muse 初始架构

## 目标

把 Muse 拆成可独立验证的模块，先做可靠的单机核心，再逐步接入模型、主动触达与外部工具。

## 模块边界

- **Core**：会话编排、用户偏好、任务路由；不直接持有外部服务密钥。
- **Memory**：分成 User Track（用户偏好/事实/任务/兴趣）与 Agent Track（Episode/Atomic Fact/Foresight/Skill/Case）；每条记录有 kind、source、confidence、revision。SQLite 保存事务状态、候选审批与历史；Obsidian Markdown 是人类可读的单向镜像，暂不作为权威源。检索支持项目、kind、track 过滤；待审候选不会进入检索。
- **Obsidian**：以普通 Markdown Vault 提供长期记忆的人类可读界面；SQLite 仍是已批准记忆的权威来源。官方 CLI 提供指定 Vault 的只读搜索/读取；Muse 安全单向创建缺失笔记，frontmatter 标记 kind/track，并以只读 audit 比对 SHA-256、ID/revision 及外部修改。任何冲突均不自动回写、覆盖或删除。- **Tools**：WebCodex、浏览器、文件和其他连接器的适配层；每个工具声明权限范围、输入和副作用。
- **Interface**：聊天入口与主动通知渠道；通知频率可配置，可暂停。
- **Audit**：记录任务开始/结束、使用的工具、结果和错误；不记录密码、令牌或不必要的个人内容。

## 建议的演进阶段

1. **本地骨架**：配置加载、健康检查、记忆数据格式、测试。
2. **对话核心**：接入一个模型提供方，加入会话上下文和明确的记忆读写策略。
3. **主动任务**：接入定时/事件触发器，先做每日简报与待办提醒；允许用户暂停和查看任务。
4. **工具执行**：按项目/目录授权接入 WebCodex；先只读，再逐项开放写入与控制。
5. **多智能体协作**：再接入 Hermes、DeepSeek、豆包、千问等，定义角色、消息协议和人工仲裁。

## 多 Agent 编排核心

- `MultiAgentOrchestrator` 接收明确注入的 Agent callable，不绑定供应商 SDK；独立角色可并行执行，硬上限 32，默认每个 fan-out/DAG wave 按实时 CPU/RAM 预算限流。
- 每次运行及每个 Agent 的 queued/running/final 状态、输出和错误写入独立 SQLite 审计表；部分 Agent 失败时保留成功结果，并将父运行标记为 partial。
- Worker 已支援持續輪詢、SQLite 任務租約、心跳續租、過期租約回收、3 次崩潰回收上限、舊 Worker fencing，以及不暴露 payload 的狀態快照 CLI；一般 handler 失敗不會自動重試。Worker 失去租約所有權時會丟棄 handler 結果，不會誤報任務成功；條件式 finish 未命中會回滾 SQLite 交易，避免鎖住進度事件寫入。Watchdog 同時檢查 Worker 心跳與本機 PID 存活（PID 重用仍可能造成誤判，後續可加入程序啟動時間指紋）。仍不含雲端適配器、流式輸出或 Windows 常駐服務。供應商接入須另行配置並遵守免費額度及憑證邊界。

## 主動事件准入与授权工作

- src/muse/intake.py 提供本機 TriggerStore、AdmissionPolicy、SourcePolicy、TriggerInput、DecisionResult；按 (source,event_id) 去重，使用 SQLite BEGIN IMMEDIATE 保護並發接收。
- Deterministic gate 先檢查 source 是否啟用及 actor 是否在 allowlist；明確 addressed request 標記 explicit，其他事件標記 inferred。模式 off/observe/auto 分開處理；observe 只記錄候選，不建立 inferred Mandate。
- inferred assessment 必須對每條 enabled rule 提供一次 boolean 判斷；auto 只有至少一條規則匹配才接受。Decision/Mandate 綁定 policy version + SHA-256 policy fingerprint，內容即使在版本字串不變時被修改也會使舊候選失效；Mandate 由 SQLite trigger 禁止 UPDATE/DELETE。Mandate 只記錄授權 scope，不等於 OS/tool permission；敏感 handler 仍由 TaskQueue approval gate 控制。
- `src/muse/intake_runtime.py` 提供 SourceAdapter/RuleAssessor protocol、本地 FixtureSourceAdapter、受限 KeywordAssessor、只讀 `GitHubPublicIssuesAdapter` 和 `muse-intake` CLI。GitHub adapter 只有在明確傳入 `--github-repo OWNER/REPO` 時才呼叫官方 HTTPS API；只讀公開 open issues、單頁最多 100 條、10 秒 timeout/512 KiB response cap，不帶 Token/Authorization header。沒有 webhook、私有 repo、IM 或背景輪詢。批次最多 100 個事件，fixture/policy JSON 有大小及欄位限制；評估器失敗後可重試未決候選，重播事件只使用首次保存的 canonical payload。- Mandate 建立與 `intake_outbox` 寫入在同一 SQLite transaction；Outbox dispatcher 用 TaskQueue idempotency key 做耐久派發，崩潰後重送不會重複建任務。派發的 `trigger_work_proposal` 使用 manual recovery，Worker 預設要求人工批准；批准後只把受限 evidence 寫成 `data/trigger_proposals/` 工作包，不執行來源 payload 內的命令。
- `ImprovementStore` 持久保存能力改进提案、研究来源、许可证/安全审查状态和状态变更事件。
- `AutonomyPlanStore` 保存本機模型提出的下一步規劃；`muse-autonomy` 可按 Backlog/脫敏隊列摘要請模型提出單一提案，嚴格驗證後去重保存。提案可由使用者批准/拒絕；批准後以 TaskQueue idempotency key 防止重複派工，Worker 會再次核對批准記錄並產生受安全邊界約束的工作包，但不會執行模型生成的任意命令。
- `SearxngSearchAdapter` 透過使用者明確配置的 SearXNG JSON endpoint 做唯讀搜尋；`ResearchNotebook` 保存查詢與來源摘要。尚未實作網頁正文擷取、可信度交叉核實或 Planner 自動調用研究工具。
- `RoutineStore` 持久保存 interval routines，以 SQLite lease + 每次 claim 的唯一 fencing token 防止多 Runner 重複執行；lease 被重新領取後，舊 Worker 不能覆寫新 Worker 結果。`muse-routines` 提供建立、列出、啟用/停用及前台 loop；`autonomy_plan` handler 只產生需批准提案。
- Routine lease 現支援 token-fenced renewal heartbeat；長時間 handler 執行期間會定期續租，finish 同時要求 token 正確且 lease 尚未過期。過期後即使尚未被另一 Runner 領取，舊 Runner 亦不能完成該 routine。這是防重複執行的租約保護，不會自動中止已啟動的 handler。
- `ReminderStore` 提供本機 SQLite 提醒收件箱：帶時區時間、due 查詢、dedupe、snooze、人工 acknowledge；`capture` 用本機受限語法解析中文/英文相對時間，支援半小時後、20 分鐘後、in 20 minutes、明天 09:30，不呼叫雲端模型。`notify-due` 使用 Windows 內置 .NET `NotifyIcon`；預設本機時間 22:00–08:00 靜音，靜音時不標記已通知，下一輪排程重試。通知只在互動式 Task Scheduler 觸發或手動呼叫時出現；OS 設定可能抑制 balloon。Watchdog 以每日 dedupe key 將失敗任務、等待審批、過期租約、15 分鐘停滯任務、失聯 Worker 的脫敏聚合訊號轉成提醒；`muse-intent` 只產生 capture 計畫，只有明確 `--create-reminder` 才寫入資料庫。
- 只有至少一條研究來源、全部來源許可證為 permissive/not_applicable 且安全審查為 reviewed，改進提案才可被明確批准；未批准不得標記 implemented。
- 研究參考 LangGraph 的 checkpoint/interrupt、OpenHands 的自動化與執行層分離，以及遞歸自我改進研究中對獨立評估器的要求；詳見 docs/SELF_IMPROVEMENT_RESEARCH.md。

## 免費額度路由

- `FreeOnlyRouter` 只选择已启用、已确认免费层、并且额度经过核实且大于零的 Provider。
- 配額未知、核驗過期、額度耗盡或只提供付費層時 fail-closed；不會自動切換付費模型。

## 安全与控制

- 所有路径必须位于明确授权的工作区内。
- 默认只读；写入、安装、删除、发送、发布和服务控制分开授权。
- 不把 Runner 的目录授权等同于允许执行任意操作。
- 不把一次工具调用成功等同于整个工作流成功。
- 对外发送内容先预览；涉及金钱或不可逆影响的动作必须人工确认。
- 凭证只从安全环境变量或密钥存储读取，不写入配置样例、日志或 Git。

## Agent 管理與安全發現

- `AgentRegistry` 以 SQLite/WAL 保存 Agent 身份、角色、能力聲明、來源、啟用狀態、最後心跳與目前任務；在線/忙碌/離線由最後心跳時間及忙碌任務狀態推導，停用狀態優先。Registry 使用執行緒鎖保護共享 SQLite 連線，適配器執行期間更新忙碌心跳並記錄實際成功/失敗結果。
- 效果指標按 task kind 聚合；任務完成率與 Reviewer pass rate 分母分離。`record_review_outcome()` 只增加審查樣本，不會重複計算已完成任務；`IndependentReviewGate` 可選注入 AgentRegistry，審查結果自動回寫 Builder 指標。SpecialistRouter 只選 online Agent，按已觀測成功率及審查通過率作有限排序加權，無樣本不加分。
- `agent_discovery.py` 只讀取使用者明確配置的 loopback HTTP `/health` 或 `/api/v1/health` URL；最多 32 個端點、每次 3 秒內、回應 8 KiB 上限、禁止 redirect，並禁用環境 HTTP proxy。可用 `--loop --interval-seconds` 在本機前台持續刷新；Ctrl+C 停止，不會自行安裝背景服務。
- 新增 `LocalHTTPAgentAdapter`：只接受明確配置的 `http://127.0.0.1` / `localhost` 固定 `/api/v1/invoke` URL，POST JSON，timeout <=30 秒、請求 <=32 KiB、回應 <=64 KiB、禁止 redirect，並核對回應 Agent ID；顯式禁用環境 HTTP proxy，確保 loopback 請求直連。`SpecialistRouter.register_local_http_agent()` 將它接入現有 trusted callable；能力由本地 profile 指定，遠端回報不會擴權。此協議需由本機 Agent 服務實作，並不代表已連接 Hermes/DeepSeek/豆包/千問。
- 桌面狀態 API 新增只讀 `GET /api/v1/agents`，只回傳顯示所需欄位及聚合指標；仍受 loopback、Bearer token、Origin、併發與請求大小邊界保護。
- `SpecialistRouter.dispatch_pipeline()` 提供 researcher → architect → developer → reviewer 的串行 DAG 模板；依賴由 `MultiAgentOrchestrator.run_dag()` 驗證和執行，階段輸出只作不可信任上下文，前序失敗會阻斷後續。所有階段必須有可信 callable、能力符合角色策略且 Agent 在線；缺一即不建立執行圖。既有並行 fan-out dispatch 保持相容。
- `MuseRuntime.dispatch_commander_once()` 是 Ruflo 路徑的 Muse Commander 入口：必須指定單一 ticket ID 並明確批准；缺少 Provider key 時在 claim 前拒絕。默认尝试 loopback-only `LocalModelCommanderPlanner`，读取 User Track 记忆并把易经/紫微/奇门/六壬作为象征性假设上下文；本地模型不可用或配置不安全时回退经校验的 `CommanderPlanner` 安全 DAG，不回退云端。`MultiAgentOrchestrator` 在同一 TaskQueue 数据库的独立审计表中执行并发 wave；TaskQueue 仍是唯一 ticket/approval/report 账本，子步骤事件和最终状态回写该账本。

## 四支柱与资源治理实现边界

- 产品宪章见 `docs/VISION.md`。玄学/象征推演只生成待验证假设；事实结论仍要求可追溯来源与独立证据。
- `CommanderPlanner`、`SpecialistRouter`、`AgentRegistry` 和 `MultiAgentOrchestrator` 是 Muse 的规划/专家选择/执行核心；`MultiAgentOrchestrator` 默认启用 `ResourcePolicy`，在每次 fan-out 或 DAG wave 前重新探测 CPU/RAM 并限制 worker slots；只有明确的压力测试可用 `resource_aware=False` 关闭。
- `resource_governor.py` 使用标准库探测逻辑 CPU 与可用物理内存；默认保留 1 个 CPU 核、2 GiB RAM，每个并发 worker 预留 512 MiB，硬上限 32。未知内存时自动并发保守降为 1；内存不足以同时满足 reserve 与一个 worker 时暂停自动派发。
- `muse-worker --auto-workers --max-workers N` 可按一次实时快照选择并发数；默认 `muse-worker` 仍保持单 worker 兼容行为。多 Agent 编排和 `ResearchPipeline` 默认启用资源感知，在每个 fan-out/DAG wave 或研究阶段前重新探测 CPU/RAM；可注入自定义 `ResourcePolicy` 调整预算，明确指定 `resource_aware=False` 才回到固定并发模式。
- 当前 GPU 显存探测未实现，始终报告 unknown；本阶段不据此增加 GPU 并发。资源快照是派发前采样，不是持续 OS 级资源监控；运行中动态降载、任务级 CPU/GPU 分类、跨进程全局配额和公平队列仍待后续实现。
- 本地 Runner 的实际硬件规格、任务平均内存和 GPU 能力必须现场测量后校准默认估算；估算值是保守调度参数，不是实测保证。

- `muse.inspiration.InspirationEngine` / `muse-inspire` 是离线灵感上下文构建器：仅检索匹配的 User Track 记忆，组合易经/紫微/奇门/六壬的象征视角、研究问题和可证伪证据清单；不做起卦/排盘、不调用模型、不写记忆。需要模型生成时，后续只允许接入经 loopback 验证的本地模型，不能静默回退云端。
- `ResearchPipeline` 复用 `SearxngSearchAdapter` 与 `PublicSourceReader`，在搜索和正文抓取两个并发阶段前分别重新探测 CPU/RAM；资源不足时 fail closed。搜索仍需显式配置 SearXNG endpoint。

## 當前實現與未實現
已實現：SQLite MemoryStore/ConversationStore、MuseAssistant、LMStudioClient/ProviderAdapter、loopback-only LocalModelCommanderPlanner（可配置 1–180 秒 timeout，預設 90 秒；本地模型已實測生成通過 CommanderPlanner 驗證的角色受限 DAG）、Ruflo LM Studio 本地 Provider bridge（loopback model discovery、Ollama-compatible routing、cloud-key stripping、fallback disabled、provider-native config.model）、TaskQueue/Worker（approval、cancel、idempotency、lease、heartbeat、recovery、progress）、RoutineStore、ReminderStore、Watchdog、AutonomyPlanStore、ImprovementStore、CapabilityRegistry、SearXNG ResearchNotebook、MultiAgentOrchestrator（最高 32 并发、partial failure；默认 ResourcePolicy 按 CPU/RAM 限流）、TriggerStore（source/event dedupe、actor allowlist、explicit/inferred、off/observe/auto、policy-version-bound Decision、immutable Mandate）、AgentRegistry/本機配置式健康探測及 Agent 狀態 API、受限本機 HTTP Agent adapter、Reviewer 結果回寫。Obsidian 官方 CLI 唯讀 adapter 與 Vault audit 已完成。
未實現：跨主機 federation、成本/延遲統一評分、真實 Ruflo Agent 端到端 live dispatch 驗收（本地 Provider 路由已接線，尚未對實際 Ticket 執行 Agent）、Tauri 桌面殼及實時推送、Hermes/DeepSeek/豆包/千問的供應商專用連接。持續健康檢查目前是需用戶在本機前台啟動的 loop，尚未安裝 Windows 常駐服務。

## 軟件研究與創造引擎（B-100）
- `muse.software_crafting.MCPStdioClient` 是 REA/Craft 共用的唯一 MCP stdio transport；精確 allowlist、固定 argv、最小環境、有界訊息、timeout 與逐工具授權。已刪除重複 MCP client，沒有第二套 transport。
- `muse.ego_browser` + `muse.browser_research` 將 ego-lite 接入既有 ResearchPipeline：只把當前課題的 support/counter 公開查詢送到 Google；HTTP reader 優先，動態頁面才用 ego-lite fallback；可在同源範圍內收集受限圖片/PDF，保留來源與權利狀態。
- `muse.software_recreation` 只產生授權感知計劃；排程、執行與恢復仍由 Muse Commander、現有 TaskQueue/Worker、Approval、TestRunner、ReviewGate、ProductBuilder 負責。
- REA 負責靜態/結構分析與證據；Craft Anything 負責已授權 Study/Check 與後續建構；ego-lite 負責公開網頁搜尋、動態頁面觀察和素材採集。桌面 app 尚未安裝，Craft 的 website Study 仍被 provider consent gate 阻擋；Build/Check 未作實機完工標記。
- 詳見 `docs/UPSTREAM_FUSION_RESEARCH.md`、`docs/SOFTWARE_RECREATION_ENGINE.md`、`docs/EGO_LITE_INTEGRATION.md`。ChatGPT 不會在對話結束後自行持續運算；長期運行仍依賴本機服務與既有授權流程。