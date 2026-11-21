# Muse 能力缺口與自主演進 Backlog

更新：2026-11-05

本文件是 Muse 的可持續工作清單。每項工作必須有可驗收結果；不得用「持續思考」或空轉輪詢冒充進度。工作可並行研究，但寫入、安裝、雲端傳輸、服務控制等副作用仍按權限邊界執行。
## P0：讓任務可持續、可恢復、可觀察

### B-001 任務租約、心跳與崩潰回收
- 狀態：第一階段完成。
- 已有：SQLite lease_owner/lease_expires_at/heartbeat_at/attempts、心跳續租、舊 Worker fencing、過期回收、3 次崩潰回收上限。
- 驗收：Worker 中止後，任務可重新領取；超過上限進入 failed；舊 Worker 不得覆寫新 Worker 結果。
- 後續：補充服務級 kill/restart 整合測試及任務取消。



### B-002 状态检视与进度事件
- 状态：队列快照、结构化事件库与 Worker 生命週期事件第一阶段完成；本機自檢 CLI 第一階段完成。
- 已有：`muse-worker --status` 显示任务状态计数及有限活动摘要；`ProgressEventStore` 持久记录 task/agent/research/test/review/system 事件；`muse-progress` 支持按类别、主体和数量查询；Worker 自动记录 started/waiting_approval/completed/failed；`muse-watchdog` 可檢查隊列、過期租約及最近進度，並留下 system 事件。
- 隐私：事件摘要限制 500 字，不接受任意 payload 对象；Watchdog 報告不顯示任務標題、payload 或租約持有人。
- 已补充：ResearchPipeline CLI 默认将 started/progress/completed/failed 事件写入同一 ProgressEventStore；摘要只含固定阶段说明和数量，不记录查询词/正文/payload，遥测故障不会阻断研究。
- 已補充：測試運行器 `muse-test` 已將 started/completed/failed 事件接入 ProgressEventStore；IndependentReviewGate 亦新增可選 started/completed/failed 事件，固定摘要及錯誤碼，不記錄 artifact/證據/模型輸出，遙測故障不影響主流程。Watchdog 現可選擇性讀取明確配置的 loopback Agent health URL，作有界唯讀 GET 探測；不註冊 Agent、不刷新心跳、不掃描端口，結果只記錄聚合計數。

### B-003 Windows 常駐服務
- 狀態：未部署。

- 依賴：B-001、B-002、資源限制、停止/暫停控制、日誌脫敏、服務恢復測試。
- 驗收：開機啟動、健康檢查、崩潰重啟、正常停止、CPU/記憶體上限及使用者可暫停。
- 權限：安裝/啟用 Windows 服務或開機自啟前，必須取得使用者明確批准；軟件及資料優先 D 盤。

### B-010 每半小時本機自檢與停滯偵測

- 狀態：Watchdog 單次檢查、每 30 分鐘 Task Scheduler、Worker 心跳/進程存活、可選本機端點健康探測及 24 小時滾動歷史趨勢比較已實作；排程仍為 Interactive only。
- 已有：`muse-watchdog --once` 檢查隊列、過期租約、15 分鐘未更新的 running task、進度事件與容量規劃；`\\MuseHourlyWatchdog` 每 30 分鐘呼叫 `scripts/muse-watchdog.cmd`；`--agent-health-config` 只探測配置內最多 32 個 loopback health URL，timeout/response size 有上限、禁止 redirect，僅回報聚合健康計數。失敗、等待審批、租約過期、停滯任務、失聯 Worker 會建立每日去重的本機提醒。
- 未有：登出狀態下運作、睡眠/關機期間檢查、實際生產 Agent/工具端點配置與定時探測接線、安全自動恢復。歷史趨勢比較相鄰 24 小時的 warning 比率與聚合指標；樣本不足時返回 insufficient_data，不作因果推斷。
- 權限：每 30 分鐘排程依使用者要求建立；改為登出後執行或服務/開機自啟須另行明確批准。

### B-011 每小時工作量估算與容量排程
- 狀態：第一階段完成；歷史耗時全局校準及 workload class 分層第二階段完成。

- 已有：任務記錄 priority 0–100 及 estimated_seconds；舊資料庫自動遷移；Worker 優先領取高優先級任務；`muse-plan-hour` 以 3600 秒窗口、300 秒保留時間進行 first-fit packing；Watchdog 每小時輸出 balanced/underfilled/no_fit。
- 已補充：TaskQueue 從最近最多 4,000 個 assignment/terminal lifecycle events 配對成功執行時長；至少 3 個有效樣本才以中位數校準新任務估算，樣本不足回退 900 秒。只讀事件時間與 succeeded 狀態，不讀取標題、payload 或 Worker 身份；異常/失敗/超過 24 小時樣本不參與估算。顯式 estimated_seconds 仍優先。
- 已補充：任務新增受限 `workload_class`（general/research/coding/media/writing/operations），舊庫遷移預設 general；每類獨立統計成功樣本及中位數，樣本不足只回退該類預設，不借用其他類樣本；新任務按類別校準。未登記類別 fail-closed。
- 未有：從 Backlog 自動生成可執行任務、執行中的硬性 deadline/協作取消。
- 驗收：高優先級優先、估算未知使用保守預設、大任務延後但不阻塞較短任務、保留健康檢查時間；不得用重複空轉填滿時間。

### B-012 每半小時成果報告
- 狀態：第一階段完成；本機 Markdown 報告及 30 分鐘排程已實跑。
- 已有：報告包含最近窗口活動統計、隊列健康、30 分鐘工作量、歷史執行估算校準樣本數/來源、最近 8 條脫敏事件時間線、最新項目進展及第一個未完成 Backlog 項；隱去任務標題、payload、事件摘要與憑證。
- 已補充：本機 Markdown 報告新增最近 8 條脫敏事件 timeline（只含 ISO 時間及格式白名單 category.event，不含 subject ID、summary、payload）。
- 未有：向 ChatGPT/手機主動推送、即時 UI timeline、報告中的程式碼差異/測試名稱明細及跨窗口趨勢圖。
- 驗收：每次排程產生唯一時間戳報告；報告內容與本機 Progress/Backlog 相符；不得將本機報告說成已推送聊天。
- 下一步：研究本機 UI 即時 timeline、跨窗口趨勢圖及安全通知通道；不得將本機報告宣稱為已推送聊天。
### B-014 模型驅動的自主下一步規劃
- 狀態：第一階段完成；批准提案的 Ticket 狀態回報第一階段完成；受限能力路由/真實業務 handler 尚未完成。
- 已有：`muse-autonomy` 可呼叫本機 LM Studio，讀取 Backlog 與脫敏隊列摘要，要求模型只提出一項下一步；嚴格驗證 JSON schema/動作類別，SQLite 去重保存；`--loop --interval-seconds 1800` 可每 30 分鐘重複規劃，Ctrl+C 停止；`--list/--approve/--reject` CLI 已實作，批准後建立 idempotent approval-gated `approved_plan_intake` Ticket。
- 新增：`execution_status` 與 `refresh_execution_status()` 使用 SQLite `mode=ro` 將 plan.task_id 對應至 TaskQueue 權威狀態；`--list` 只讀刷新回執，顯示 queued/running/succeeded/failed/waiting_approval/cancelled/missing，不會 claim/retry/派工，也不會創建缺失的任務 DB。
- 安全：模型輸出一律是未批准提案；批准只授權建立安全 intake Ticket，不代表允許任意 shell/安裝/刪除/外發/OS 修改。外部行動仍需獨立批准。
- 未有：Routine handler 的自主規劃背景註冊、成熟業務 handler 接入及真實執行回報；目前 intake 只保存安全工作包。
- 已新增：`ApprovedPlanCapabilityRouter` 可信本地 handler 注入式路由骨架，只允許 read_only/research/code_change；external_action 和未註冊 handler fail-closed；handler 接收 plan-scoped idempotency key，結果限 64 KiB JSON。
- 執行前重新執行完整 plan schema 驗證，額外字段不會傳給 handler。此為路由框架，不代表已接上成熟業務 handler 或真實自主執行。
- 驗收：模型輸出經 schema 驗證、重複提案去重、未批准提案不會執行；批准 Ticket 的 queued→succeeded 回執與 missing DB 不建立測試 **11/11 通過**；loop 可優雅停止及隔離單次錯誤。
### B-015 網上搜尋與研究來源收集
- 狀態：第二階段完成、第三階段部分完成（SearXNG/Bing RSS 搜尋 + 受限正文閱讀 + SQLite 證據/歸因存儲 + 資源感知並發 + 研究進度事件 + 詞項相關度提示）；來源可信度評級、跨來源事實核驗和 Planner 自動調用仍未完成。
- 已有：`muse-research` 使用明確配置的 SearXNG endpoint；`BingRSSSearchAdapter` 可由 `muse-research-pipeline --provider bing-rss` 明確選用且無需 API key。限制查詢長度/結果數/回應大小，過濾非 HTTP(S) 及含憑證 URL，保存 query/title/URL/snippet/engine/published_at 歸因。
- 配置：透過 `MUSE_SEARXNG_URL` 或 `--endpoint` 指定使用者選擇的 SearXNG 服務；不暗中假設或呼叫第三方搜尋供應商。
- 安全：只傳送命令列明確提供的公開查詢；搜尋結果視為不可信資料；不自動打開結果頁、不下載、不執行程式碼、不上傳私人文件。
- 已有：`ResearchPipeline` 并行执行多个搜索角度与公开正文抓取，按角度轮询并优先未见域名；保存正文哈希/时间/截断信息及可迁移的 relevance_score/relevance_method。以标题/摘要词项重合给出 0–100 相关度提示，并输出 HTTPS、摘要/日期、正文长度等信号；研究开始/部分完成/完成/失败写入同一 ProgressEventStore。搜索与抓取阶段分别按实时 CPU/RAM 预算限流，资源不足时暂停派发。
- 未有：基于可核验证据的来源可信度评级、日期交叉核实、事实级引用报告、定时监察及 Planner 自动调用研究工具。词项相关度只衡量 query/title/snippet 匹配，不能代替可信度。
- 已补充：ResearchPipeline 以常见多级后缀规则生成 registrable-domain grouping hint，合并同一注册域下的子域；检测正文 SHA-256 完全重复；自主研究高价值提醒要求至少两个启发式发布者域分组。来源独立性/可信度仍标记 unassessed，不把分组启发式当作组织独立性或信誉评级。


### B-017 本地长期记忆与对话上下文
- 状态：SQLite MemoryStore、候选提炼/人工审批第一阶段完成；本轮加入 typed memory + dual-track，Obsidian Markdown 单向镜像同步记录类型。
- 已有：profile/preference/fact/task/interest/episode/atomic_fact/foresight/skill/case 类型；user/agent 双轨隔离；来源/时间戳/置信度、修订历史、软删除、JSON 导出；关键词部分匹配、中文双字词及 kind/track/project 过滤；MuseAssistant 只把相关已批准记忆作本轮临时上下文；候选需人工批准才进入正式库。
- 迁移：旧 SQLite 自动添加 kind/track 列，既有记录安全默认 fact + user；候选审批保留类型与轨道，修订历史逐版本保存类型/轨道；Obsidian frontmatter 输出 kind/track。
- 隐私：提炼仅在显式 opt-in 且使用本地模型时启用；私人对话不发送云端；候选待审不参与检索；Agent Track 不自动注入用户个人上下文。
- 未有：Obsidian 双向同步、人工编辑回流、冲突解决、过期/冲突记忆治理、语义向量检索及低频 consolidation / Reflection。
### B-018 Obsidian 長期記憶 Vault 與 Agent 連接
- 狀態：第一階段完成（官方 CLI 只讀搜索/讀取 + SQLite→Markdown 安全鏡像），並新增唯讀稽核。
- 本機：Obsidian 1.14.5，安裝於 `D:\\Program Files\\Obsidian`；CLI `Obsidian.com` 已啟用。Vault：`D:\\AI AGENT\\Muse\\vault`，CLI Vault 名稱 `vault`。
- 已有：`obsidian_vault.py` 安全匯出與 audit；`obsidian_cli.py` 官方 CLI version/path/search/read；`muse-memory export-obsidian`、`obsidian-search`、`obsidian-read`、`audit-obsidian`。無新增第三方 Python 依賴。
- 安全邊界：SQLite 是權威源；匯出只建立缺失文件；audit 只讀比較 SHA-256、frontmatter ID/revision，標記外部修改、缺失、格式錯誤及孤立 managed note；不回寫 SQLite、不覆蓋/刪除 Vault 文件。
- 開源選擇：優先 Obsidian 官方 CLI；暫不安裝 `obsidian-local-rest-api` 或其他 MCP 插件，因官方 CLI 已滿足現階段需求。
- 下一步：持久化首次匯出 hash/last-seen hash，增加逐筆 diff、人工批准回流及可回滾合併。

## P1：建立真正的自我改進閉環
### B-026 Scrapling 自适应网页采集与解析能力
- 状态：第一阶段完成（可选依赖、CSS/XPath、自适应 selector、workspace SQLite、单页结构化输出 20,000 字符上限、PublicSourceReader → ResearchPipeline 可选接线）；Scrapling 0.5.1 已安装到 D 盘项目 `.venv`；10/10 适配器/Reader 测试、61/61 Research Fetch/Pipeline/Browser 回归、11/11 dashboard 回归通过，真实 Selector API smoke 通过。CLI 需显式 `--scrapling`，默认关闭；未启用 Scrapling 网络 Fetcher、动态浏览器、Spider 或 MCP。全量 1,637 测试首轮受环境代理影响有 1 项失败，修复测试代理隔离后定向通过；第二轮全量任务因 Runner 替换丢失，整体仍待全量复验。
- 定位：Scrapling 仅作为 Muse Research 的能力提供者；Commander、TaskQueue、ResourceGovernor、ResearchEvidenceStore 和审批/证据链仍由 Muse 统一控制。
- 下一步：在获准的公开测试目标上完成端到端研究 smoke，核对结构化字段、来源归因和知识图谱落库；随后评估在 PublicSourceReader 安全边界内接入 Fetcher，并与现有 ego-lite/Playwright 做动态页面基准比较；最后才考虑受 Commander 限额的 Spider。
- 安全：默认不启动 Scrapling MCP，不启用代理轮换、登录态复用或反自动化绕过；尊重 robots.txt/条款/限速；遇到访问挑战、登录或付费墙停止。

### B-016 持久化 Routine Scheduler
- 狀態：第一階段完成（SQLite interval routines、lease、防重複 claim、fencing token、heartbeat renewal、enable/disable、前台 loop）。
- 已有：`muse-routines add/list/enable/disable` 管理 routine；handler 運行期間以 token-fenced heartbeat 續租；finish 要求 lease 尚未過期且 token 仍有效；過期或被重新領取的 lease 不能由舊 Runner 完成。舊資料庫 `lease_token` 遷移現以 `BEGIN IMMEDIATE` 串行化，並容忍並發首次開啟時 WAL PRAGMA 的 locked 競爭。
- 安全：lease 防止多 Runner 重複 claim；heartbeat 降低長任務 lease 過期風險；fencing token 阻止 stale worker 覆寫結果。heartbeat 失敗不會假裝成功，也不會自動中止 handler。
- 未有：背景服務註冊、手機通知、cron/時區/重試政策、跨程序優雅停機及 handler 強制取消。

### B-019 本機提醒收件箱
- 狀態：第一階段完成（SQLite 本機提醒、到期查詢、去重、snooze、人工確認、受限自然語言 capture、Windows 通知區 balloon 投遞）。
- 已有：`muse-reminders add/list/due/ack/snooze/capture/notify-due`；支援中文/英文相對時間與明天時鐘時間；到期時間以機器本地時區或明確 IANA timezone 解析；預設 22:00–08:00 靜音，靜音期間不標記 notified，下一輪排程重試；Watchdog 從脫敏聚合狀態建立每日去重提醒。
- 隱私：只寫入本機 SQLite，不外傳；自然語言 capture 使用確定性本機 parser，不把私人文字送雲端；intent CLI 只有明確 `--create-reminder` 才保存。
- 未有：手機推送、可配置多段勿擾日曆、重複提醒規則及 routine/一般聊天的自動入口整合；通知中心設定可能抑制 balloon，呼叫成功不保證畫面一定顯示。

### B-020 事件准入與授權工作生命周期（dsh-superagent 融合）
- 狀態：第三階段完成（本機 fixture + 明確指定的公開 GitHub Issues read-only adapter + deterministic structured assessor + transactional outbox + TaskQueue approval-gated handoff）；未接 webhook、私有來源或雲端模型 Assessor。
- 已有：src/muse/intake.py 與 src/muse/intake_runtime.py；按 source/event_id 去重；source enabled + actor allowlist；explicit/inferred 分流；off/observe/auto；inferred assessment 必須逐一評估所有 enabled rules，auto 只接受至少一條規則匹配；Decision/Mandate 綁定 policy version + SHA-256 policy fingerprint；scope allowlist；SQLite trigger 禁止 Mandate UPDATE/DELETE。
- 執行鏈：SourceAdapter/RuleAssessor protocol、本機 FixtureSourceAdapter、KeywordAssessor、GitHubPublicIssuesAdapter；GitHub 僅在明確指定公開 repo 時做單頁 GET，最多 100 條、10 秒 timeout、512 KiB response cap，不帶 Token；Mandate + Outbox 同 transaction；TaskQueue idempotency key；`trigger_work_proposal` 預設人工批准，Worker 批准後只生成本地安全工作包。
- 安全：示例 policy 預設 observe；fixture/adapter 最多 100 個事件、單事件 payload 最多 16 KiB；拒絕事件只保留 metadata。無 webhook、私有 repo、IM、常駐輪詢或命令執行；Mandate 不取代 TaskQueue 對敏感 handler 的人工批准。
- 驗收：事件/Outbox 並發去重、actor spoof 拒絕、policy version/content 變更失效、observe 不派工、auto 全規則評估及匹配、scope 邊界、Mandate 不可修改/刪除、assessor retry、outbox replay、GitHub request no-auth/timeout/size/PR filter、CLI 輸出不洩漏 payload；新增 11 項 Runtime、13 項 Intake 和 12 項 Worker 測試。
### B-004 能力缺口自動發現

- 狀態：B-004 第一至第四階段完成；已具備 metadata-only 任務/測試/重試失敗訊號、穩定失敗分類、結構化使用者回饋、重複修正標籤、桌面回饋表單、手動雷達掃描及跨窗口趨勢比較第一階段。標籤校準仍未接入。
- 運行訊號：muse-radar 按可配置 lookback window 聚合 TaskQueue 失敗/重試指標、ProgressEventStore 測試/任務事件及穩定 failure code；不讀取任務標題、payload、異常摘要、私人對話或原始文件。
- 失敗分類：Worker 將常見異常映射為穩定 code、category、retryable 建議及固定 recovery guidance；自訂 MuseTaskError 可聲明受驗證 code。任務結果與 ProgressEventStore 只保存分類資料，不保存原始 exception message；retryable 目前只是提示，不會自動重試。
- 使用者回饋：CLI 與桌面表單只接受 allowlisted category/tag 和 severity 1–5，不接受自由文本或身份資料；支持 repeated_manual_fix/repeated_retry 等重複修正標籤。
- 提案觸發：最近窗口內回饋總數 >=2 或最高 severity >=4 時建立 user_feedback 提案；重複修正/重試標籤 >=2 時建立 repeated_correction 提案。提案附 evidence、priority、confidence、impact、acceptance criteria、rollback plan，按 priority 排序並去重。
- 桌面 API：GET feedback/proposals 僅暴露展示白名單欄位；POST feedback 僅接受結構化標籤；POST radar/scan 由使用者明確觸發 24 小時本地掃描。API 維持 loopback/Bearer/Origin/body-size 限制。
- 安全與遷移：ImprovementStore 舊庫自動增加 priority/metadata_json 及 feedback 表；ProgressEventStore 自動增加 error_code；舊資料安全預設 priority=50、metadata={}、error_code=NULL。所有提案仍須人工研究/審批，絕不自動實施。
- 已補充：ImprovementStore 持久化能力雷達每次掃描的 allowlisted signal counts，最多保留每個 lookback window 最近 1,000 個樣本；比較當前窗口與至少相隔一個 lookback 时长的最近历史样本，避免拿重叠窗口冒充前后对比。輸出 improving/worsening/stable/insufficient_data、總量 delta 及每個 signal delta；Status API 的手動掃描回傳趨勢摘要。
- 未有：標籤校準；設計經審批的 retry policy；補提案詳情、來源研究和審批 UI，避免把 retryable 標籤直接當成自動重試授權。
### B-005 獨立審稿 Agent 與基準測試
- 狀態：核心審查閘門、受控 code_change Worker handler、隔離 TestRunner/ExperimentBridge adapter、可選 CLI wiring、已批准提案的本地 patch-preview/Ticket 生成，以及 SQLite 進化 Journal/崩潰恢復已實作；真實雙模型 Worker smoke、Reviewer 品質校準及全自動背景觸發尚未完成。
- 已有：`src/muse/review_gate.py`；Reviewer ID 必須與 builder ID 不同；審查輸入包含 artifact、test evidence、lineage；輸出必須符合結構化 JSON；critical/high finding 強制否決；SQLite 持久保存審查結果。Reviewer 輸入/輸出均有大小與數量限制。
- Worker：`run_once()` / `run_forever()` 可注入 `execute_task(payload, task_id=...)` handler；`ApprovedCodeChangeTicketHandler` 會重讀目前 running Ticket，要求 payload 與資料庫完全一致，並驗證 `human.decision=approved` 事件與 approver actor；偽造 `_approved=True` 不會執行。只有 `implemented` 才標記 succeeded，未晉級結果保留結構化結果並標記 failed。
- TestRunner/ExperimentBridge：`build_isolated_unittest_adapter()` 為 baseline/candidate 分別註冊可比較 manifests，記錄測試命令、cwd、環境、測試檔與程式碼 SHA-256；每次測試都在臨時 workspace snapshot 中執行，`cwd` 與 `import_root` 均指向副本，避免測試寫入正式 workspace。舊 TestRunner 預設呼叫保持兼容。
- ImprovementStore：`validate_and_promote()` 綁定持久 baseline/candidate run refs、相同 test kind/argv/cwd/environment manifest、前後不同 SHA-256 程式碼指紋、測試指標與精確 ReviewStore 記錄；candidate 必須 succeeded、測試數不少於 baseline 且 failures/errors=0；Reviewer 必須不同於 implementer，且只有該 implementer 可晉級。
- 安全與恢復：受控 patch 限定 workspace 內既有 UTF-8 Python 檔案；baseline 後、candidate 後、review 後均核對 SHA-256；rollback 僅在所有目標仍匹配本次候選 hash 時原子恢復。`EvolutionJournal` 在修改前持久化原始 bytes/hash 與候選 hash，Worker 啟動/執行前恢復中斷事務；未知並發修改會標記 blocked 並保留現場，不覆蓋使用者內容。
- CLI：`muse-worker --enable-evolution --reviewer-model MODEL_ID [--reviewer-base-url http://127.0.0.1:PORT/v1]`。Builder 必須由 `config/muse.toml` 指向本地 llama.cpp/LM Studio；Reviewer 必須是不同且在指定 loopback endpoint `/v1/models` 中可用的模型 ID；沒有獨立 reviewer model 時 fail-closed。該選項不會自動建立或批准 code_change Ticket。
- 未有：自主研究/能力雷達自動觸發 `prepare-ticket`、真實本機 Builder→人工審批→獨立 Reviewer Worker 全鏈路 smoke、審批 UI、Reviewer 品質校準、網絡隔離/低權限 OS sandbox。
- 驗收：`test_evolution_executor` **12/12**、`test_worker` **26/26**、`test_test_runner` **7/7**、`test_improvement_patch_planner` **7/7**；最新 Python 3.11 全量 unittest **698/698**，`compileall` 通過。non-Git workspace 的 Runner source freshness 仍為 unproven。

### B-021 Agent 角色與能力准入策略
- 狀態：第一階段完成（SQLite 角色策略註冊與編排前准入校驗）。
- 已有：AgentRoleRegistry 持久化角色、允許聲明的能力清單及 trust tier；AgentSpec 可聲明 requested_capabilities；run/run_dag 在建立任務前拒絕未登記角色或超出角色清單的能力。
- 邊界：這是角色/能力聲明的准入控制，不是任意 Python callable 的沙箱，也不代表底層工具執行時已完成隔離。真正的工具調用授權仍需在受控 Tool Gateway 執行層檢查。

### B-022 Muse Super Agent 桌面調度中心（Cyber-Mythic Pixel UI）
- 狀態：Tauri 2/Rust 桌面殼與 Svelte 控制台已實作；本地 API v1 支持狀態讀取、結構化回饋、手動能力掃描、提案詳情、跨窗口雷達趨勢與事件列表。UI 使用 5 秒有界輪詢刷新總覽，另以 Bearer fetch-stream 接入有界 SSE 事件流，Last-Event-ID 續接、muse-reset 快照重同步、AbortController 卸載清理均已實作。來源研究/人工審批、任務操作尚未完成。
- 目標：把 Muse 從 CLI/本地服務升級為可直觀操作的桌面調度中樞，統一查看和管理 Agent、DAG、任務、審批、記憶、工具連接及事件時間線。
- 視覺：Cyber-Mythic Pixel；墨黑/夜藍/深紫底色，玉青/霓虹藍/琥珀金狀態色，像素雲海、飛檐、星軌、符紋與山海神獸；中央靈樞展示真實 Agent 節點和 DAG。可讀性、減少動態效果和真實數據優先。
- 安全：僅 loopback、隨機會話憑據、Origin 校驗、敏感字段脫敏；前端不得直接訪問 SQLite 或 shell；默認本地優先，不開放公网/LAN 控制。
- 部署：客戶端源碼和工具鏈優先放 D 盤；安裝 Rust/Node 工具鏈及依賴前確認磁盤空間和用戶授權；不註冊服務/開機自啟。
- 設計文檔：`docs/UI_PRODUCT_SPEC.md`。

### B-023 本地只讀狀態 API 與前端視覺原型
- 狀態：第一階段完成（stdlib HTTP API、接口契約、Tauri 生命周期與真實 UI 數據）；現已增加受限結構化 feedback 寫入、手動 radar scan、跨窗口趨勢回傳、5 秒 overview polling 及有界 SSE 事件流 fetch-stream 客戶端，不是通用任務執行 API。
- 已有：`muse-status-api` 命令入口；只綁定 `127.0.0.1`；隨機 Bearer token；Origin 校驗；任務快照不含 payload；能力列表不暴露 executable 路徑及 opaque metadata；GET 展示字段白名單；feedback POST 僅允許固定標籤與 severity 1–5；radar scan 僅手動觸發 metadata-only 分析。
- 資源邊界：HTTP 請求並發上限 8、隊列長度 16、socket timeout 5 秒；請求 body 上限 4 KiB；SSE 每次連線 3.5 秒上限，最多讀取最近 100 條事件，過期游標發送 reset；超出並發額度直接關閉連接，不無限創建線程。
- 前端：`ui-prototype/` 使用 Cyber-Mythic Pixel 風格；事件流以 fetch + Bearer header 接入，Last-Event-ID 重連；元件卸載 abort；事件 payload 不含 subject ID/summary。
- 後續：提案來源新增/審查/人工審批、Agent run 列表、統一 API 認證生命周期、Tauri sidecar 管理；補充真正的自主研究調度與多 Agent 執行閉環。
- 驗收：Status API 14/14；桌面 Node 測試 11/11；Svelte check 0 errors/0 warnings；Python 3.11 全量 unittest 601/601 通過；不安裝依賴、不自動啟動服務。

### B-024 專家目錄與智能任務路由 / Agent 管理
- 狀態：第二階段核心切片完成；持久化 Agent Registry、心跳/狀態、效果指標、配置式本機健康探測、Commander DAG 本機路由及 Ruflo runtime adapter 已接線。2026-11-04 已實測 LM Studio 本地模型生成角色受限 DAG，並新增 Ruflo Ollama-compatible LM Studio 本地 Provider 路由；真實 Ruflo Agent live dispatch 尚未驗收。
- 已有：SpecialistRegistry 保存專家角色/領域/關鍵詞/能力聲明；SpecialistRouter 可選接入 AgentRegistry，要求可信 callable 注入、能力子集匹配及 Agent online 才可派發；停用 Agent 不參與路由。
- Agent Registry：`src/muse/agent_registry.py` 保存身份、角色、能力聲明、來源、啟用狀態、最後心跳/當前任務；online/busy/offline/disabled 狀態由資料推導。適配器執行期間記錄 busy heartbeat，成功/失敗按 task kind 持久化；效果分數只使用實際觀測數據，沒有樣本不加分。
- Discovery：`src/muse/agent_discovery.py` 只探測明確配置的 loopback HTTP health URL，最多 32 個端點、3 秒 timeout、8 KiB response cap、禁止 redirect；不掃描端口/進程、不執行未知程序。健康服務聲明的 capabilities 僅是 metadata，不構成工具授權；示例配置預設 disabled。
- 桌面數據：`muse-status-api` 新增只讀 `/api/v1/agents`，輸出 Agent 狀態與聚合效果數據；仍沿用 loopback + bearer + Origin 防護。
- 已有：`SpecialistRouter.dispatch_pipeline()` 提供本機 callable 的 researcher → architect → developer → reviewer DAG；`MuseRuntime.dispatch_commander_once()` 把單一明確批准的 TaskQueue ticket 交給 Commander，默认先尝试 loopback-only `LocalModelCommanderPlanner`，仅使用 User Track 记忆和象征性灵感上下文生成 DAG；模型不可用时回退安全模板，不访问云端。使用 `MultiAgentOrchestrator.run_dag()` 验证依赖、并行执行研究/架构阶段、传递不可信上游输出、阻断失败下游，并将子 Agent spawn/执行/报告及最终验收写回同一 TaskQueue 事件账本。Ruflo 通过已批准的 tool allowlist 提供执行。
- 尚未完成：真实 Ruflo Agent 端到端 live 验收（需要单 Ticket 明确批准）、自动维持跨程序心跳、Hermes/DeepSeek/豆包/千问真实 endpoint adapter、Agent 延迟/成本测量、Reviewer 真实审查结果自动回写、Tauri/Rust 桌面客户端与 SSE/WebSocket。
- 安全：資料庫不保存或動態執行 Python callable；發現器只連接配置內的 loopback HTTP URL；LM Studio Ruflo 路由只允許 loopback endpoint、剝離雲端密鑰並禁用 fallback；未知/失聯 Agent fail-closed。
- Commander CLI：`muse-runtime commander-once --task-id TICKET_ID --approve` 只執行指定 ticket；缺少明確 ticket ID、approval 或有效 Provider endpoint/credential 時不 claim 任意佇列工作。

- 32 路编排验收（2026-11-08）：`MultiAgentOrchestrator.max_workers` 默认/硬上限为 32；Barrier(32) 压力测试证明 32 个 callable 同时运行；生产路径由 ResourceGovernor 按 CPU/RAM 快照限流。当前 12 逻辑 CPU 主机建议约 11 Worker，llama.cpp `--parallel 1` 限制单实例同时推理数。32 路逻辑编排已验证；32 路模型推理未验证，且当前 WebCodex ACP CodingAgent provider 未登记。
- 32 路 DAG wave 验收补充（2026-11-08）：新增 Barrier(32) DAG 测试，证明 `run_dag()` 的无依赖并行 wave 中 32 个 callable 同时运行并将 32 个结果持久化；Python 3.11 全量 unittest **736/736 通过**。此项是 Muse 内部执行层证据，不等同 ACP Provider/CodingAgent 或多路模型推理。
- 任务队列真实 32 Worker 压力测试（同一全量套件）：`run_parallel_once()` 通过 32 个独立租约 Worker 同时执行 32 个 Barrier handler；`run_parallel_forever()` 通过 32 个常驻 Worker loop 同时处理 32 个任务并验证全部退出。CLI 提供资源感知 `--auto-workers --max-workers 32`，实际 worker 数由 CPU/RAM 决策，不保证恒为 32。
- 生产队列现场状态：queued=0/running=0/waiting_approval=0；安全 CLI 探测选出 11 Worker（CPU cap 11、memory cap 52、GPU unknown），没有任务可消费，因此不启动无任务常驻循环。
- Worker CLI 默认并发调整：未传 `--workers` 时默认按 ResourceGovernor 选择 worker 数，硬上限 32；显式 `--workers N` 仍是运维人员明确指定的固定并发模式。新增测试验证默认资源感知派发；Worker 专项 27/27、全量 736/736 通过。
- 2026-11-08 B-044：已新增本地 llama.cpp/LM Studio loopback 模型健康探测，并接入 Watchdog 的 opt-in `--model-workspace`；真实端点健康、目标模型存在。仅告警，不自动重启；下一步需先补进程所有权/安全恢复/退避和 Windows 服务生命周期。
- 2026-11-08 B-045：已在本机 OpenCode 1.19.3 上完成 ACP v1 `initialize`/stdio 握手烟测；确认 Runner `runner.toml` 尚无 `[acp]`、Provider inventory 为空。下一步：在 Runner 受控配置边界注册 OpenCode Provider（`max_concurrent_runs=1`、显式环境变量映射），校验并重启 Runner；随后执行只读 coding-agent smoke、观察结构化事件并独立验收。当前未注册、未触发模型调用、未启用独立 Coding Agent。
- 2026-11-08 B-045 追加：定向 32 路编排/路由测试 36/36 通过（Python callable 层，不是 ACP Agent）；Runner `runner_config_control` 能力位存在，但公开 `check_runner_config` 只校验、`reload_runner_config` 只激活现有候选且不写文件，当前 Host 工具清单无 Runner config writer；`D:\Tools\WebCodex` 仍不在 Runner `allowed_roots`。保持 fail-closed，不借 Muse Project shell 或 GUI 绕过。
- 官方 Desktop 路径补充（2026-11-08）：Desktop guide 已记录 Extensions → Coding agents 配置类别，可写入 Desktop-owned local Runner 的编码 Agent 设置；远程/独立 Runner 仍只读。当前只观察到 WebCodex runtime/server 窗口，没有确认 Desktop 主窗口或 Runner 的 Desktop 管理归属，未进行 UI 修改/重启。需先确认管理归属与 UI 是否支持 ACP provider 字段；Tool Plugin 与 ACP Coding Agent 是不同机制。
- OpenCode 启动环境补充证据：清空子进程环境后显式映射 10 个当前存在的 Windows 运行环境变量，ACP `initialize` 与 Muse cwd 的 `session/new` 成功；临时 session 的 `mode=plan` 配置设置成功。尚未执行 prompt，不能据此认定模型调用/编码执行可用。

### B-007 研究与开源吸收管线
- 状态：第二阶段完成、第三阶段部分完成（SearXNG/Bing RSS 搜索 + 受限正文阅读 + SQLite 证据/归因存储 + 资源感知并发 + 研究进度事件 + 词项相关度提示 + 发布日期/快照引用）；来源可信度评级、跨来源事实核验和 Planner 自动调用仍未完成。
- 流程：问题 → 搜索公开来源 → 评估可信度/日期/授权 → 摘要与差异分析 → 提案 → 隔离测试 → 独立审查 → 批准。
- 本轮已完成两个上游的源码级只读审查；详见 `docs/UPSTREAM_FUSION_RESEARCH.md`。dsh-superagent MIT，吸收架构与 policy/effects/outbox 模式；SAP AGPL-3.0，不复制代码，仅保留独立 MCP/API bridge 的可选方案。
- ResearchPipeline 对缺失、无法解析及未来发布日期明确标记；每个来源快照绑定 URL + 正文 SHA-256，报告展示 citation_id、发布日期状态/日期/age_days、抓取时间与哈希。日期与 HTTPS 均不是来源真实性证明。
- 未有：可核验证据支持的来源可信度分级、跨来源事实核验、事实级引用报告、定时监察及 Planner 自动调用研究工具。词项相关度只衡量 query/title/snippet 匹配，不能代替可信度。
- 边界：默认只读；不任意 clone、安装、执行或上传私人资料。
- 2026-11-20 Scrapling/B-202：Runner 恢复后确认外网 E2E 阻塞点是系统代理 `127.0.0.1:7890` 当前拒绝连接，而非 Scrapling parser；代理路径下 Google/Cloudflare DoH 请求失败，受控直连 DoH 对照均 HTTP 200。Muse 保持 fail-closed，不自动绕过系统代理或关闭 SSRF/DNS 防护。ResearchPipeline→ResearchKnowledgeStore `structured_extract` 集成回归验证来源 metadata 入库且不复制原始正文/HTML；结构化结果显式标记 `content_trust=untrusted_page_derived`、`verification_status=not_independently_verified`。Scrapling + Research Fetch + knowledge integration 专项 25/25，新增 adapter/pipeline 两组 41/41，compileall 通过；全项目 Python 3.11 unittest 最新复验 1,651 tests，9 skipped，0 failures / 0 errors。动态浏览器不重复造轮子：现有 `ego-lite` + `HybridSourceReader` 已提供动态 DOM、截图与受限资产采集；Scrapling DynamicFetcher/Playwright 暂不安装。真实网站 E2E 待代理服务恢复后复验。
- 2026-11-20 Scrapling trust follow-up：adapter 保留 CSS/XPath selector_specs，并为结构化结果附加 `instruction_authority=false`、`requires_source_verification=true`、`promotion_eligible=false`。ResearchKnowledgeStore `transition_claim` 要求 source 证据具备方向匹配的 candidate、review 与 verified citation anchor；`contested` 同时需要支持/反驳审查。专项 11/11 + 31/31 + 39/39，compileall 通过；全量回归 1,697 tests，9 skipped，0 failures。psmux 初次锁访问 PermissionError 后，后续 detached 执行恢复成功，最初根因仍未证实。真实外站 Scrapling E2E 未验收。
- 2026-11-20 B219 验收补记：Scrapling + ResearchPipeline + ResearchKnowledgeStore 专项 **83/83 passed**，`compileall` 通过；Windows 视频追踪测试显式关闭 SQLite 连接后，Python 3.11 全项目 **1,741 tests，OK，1 skipped，0 failures / 0 errors**。psmux busy 由同一 Session 已运行 Job 持锁导致；等待自然完成后继续执行，没有改 ACL 或绕过单 Job 锁。单站外网 smoke 已由 B209 验证，多站点/重定向/故障注入仍待补。
- 2026-11-20 B209 adjacent fixes：Video Attempt Ledger 新增 `manifest_sha256` additive migration、attempt persistence/idempotency/event provenance；VideoTaskWorker 强制校验 MP4/package/manifest digest，缺失或格式无效均失败关闭。Lightpanda capture 先执行 workspace containment 与目录覆盖检查，再检查 Playwright 可选依赖。专项 VideoAttemptLedger 8/8、VideoTaskWorker 13/13、Lightpanda 6/6、BusinessOpportunity 14/14；全项目 1,697 tests，9 skipped，0 failures。未安装 Playwright，也未启动 GPU。
- 2026-11-20 Scrapling real-site smoke：使用单次进程代理 `127.0.0.1:10808` 读取 `quotes.toscrape.com`，HTTP 200；Scrapling 解析标题 1 项与 quote 10 项；来源 metadata 写入临时 ResearchKnowledgeStore，untrusted provenance 保留且原始 HTML 未入库。仅修改该子进程环境变量，没有更改系统代理。单站 E2E 已通过；更广泛的多站点/重定向/故障注入矩阵仍待补。
### B-008 隔離沙箱與可回滾改動
- 已有：Windows per-process + aggregate job memory limit、active-process cap（默认 16）、kill-on-job-close；无法套用 OS 限制时 fail-closed。批准代码改动限制在 workspace 内现有 Python 文件；baseline/candidate/review SHA-256 复核、并发修改保护、SQLite Journal 崩溃恢复，以及测试工作副本隔离均已测试。
- 未有：网络默认封闭、文件系统/用户权限隔离、CPU 硬限制、第三方程序低权限账户与人工冲突处理 UI。临时 workspace copy 不是 OS sandbox，测试子进程仍可访问网络和权限允许的绝对路径；Windows Job Object 不提供网络隔离。
- 驗收：`test_evolution_executor` **12/12**、`test_worker` **26/26**、TestRunner **7/7**；历史 Python 3.11 全量 unittest **698/698**，compileall 通過。
- 下一步：引入可验证的低权限执行身份、网络封闭与文件系统 ACL；再补人工冲突恢复 UI 和真实双模型端到端 smoke。
### B-009 Muse 協作橋接
- 狀態：未驗證 ChatGPT 新對話能否自動取得本機 Muse 程序。
- 方向：提供明確的 MCP/本機 API/CLI 啟動與健康檢查協議；助手有權限時可委派研究、任務規劃及進度讀寫。
- 驗收：每次調用可確認連線、請求範圍、結果與審計紀錄；不可連線時明確降級，不假裝已調用。
- 限制：新對話是否能自動連接，取決於當時客戶端與工具權限，不能單靠專案文件保證。

### B-013 自然語言任務入口與能力路由
- 狀態：分類 + 可審核計劃 + 持久化 Capability Registry + FFmpeg adapter + Worker handler + plan-to-queue 第一階段完成；尚未接入 ChatGPT 安全橋接。
- 已有：`muse-intent` CLI；支援影片/音訊/圖片/報告/文案/網站/程式/研究意圖；新增 `CapabilityRegistry` 持久保存本機工具路徑、功能、trust/license/status/version/requirements；`muse-media` 已確認的本機工具可同步；`muse-capabilities` 可查詢登記。
- Adapter：`ToolAdapter` 協議及 `FFmpegTranscodeAdapter` 已實作 validate/preview/execute/cancel/verify/artifact 收集；factory 只允許 ready 的 `media.ffmpeg`，未批准不啟動，輸出必須存在且非空。
- Worker：`build_adapter_handlers()` 提供 `ffmpeg_transcode` handler；`muse-worker --enable-media` 才會載入，該任務種類強制 approval gate；逾時取消子程序，輸出驗證失敗標記任務 failed。
- Plan-to-queue：`muse-task enqueue-transcode` 先 dry-run preview，再建立未批准任務；Worker 將任務移至 waiting_approval 後，使用 `muse-task approve` 明確批准，再執行。入隊不等於批准或執行。
- 路由規則：只使用 registry 中 status=ready 且功能匹配的工具；unverified/missing 不會被當成可執行能力；登記本身不會啟動 executable。
- 未有：ChatGPT → 本機安全橋接、其他工具 adapter、Worker 重啟後子程序恢復、自然語言歧義澄清及 LLM structured plan。
- 原則：不把簡單關鍵字分類當成真正理解；ChatGPT 提供結構化方案，Muse 驗證 schema、權限及工具存在性後執行；未核實能力不執行。
- 驗收：一句話可產生可預覽任務計劃；批准後調用對應本機工具；輸出檔案經驗證並回報；高風險操作可審批/拒絕/取消。真實媒體轉碼尚未實測。
### B-018 Ruflo-inspired Swarm DAG 与 Agent Message Bus
- 状态：第一阶段已实现核心库能力；尚未接入真实 Provider/LLM、CLI、Worker 持久化调度和独立 Reviewer。
- 已有：`AgentSpec.depends_on`；`MultiAgentOrchestrator.run_dag()`；依赖图缺失节点/重复边/环检测；按拓扑层并行执行；上游成功结果显式传递给下游，并标记为不可信任务数据；上游失败时下游自动 blocked；SQLite `agent_messages` 记录 started/completed/failed/blocked 事件与交接内容。
- 兼容：既有 `run()` fan-out 接口保持不变；Agent 仍由调用方注入，不擅自连接网络或调用未登记工具。
- 安全边界：消息总线是当前单次编排内的持久审计/交接记录，不是任意跨 Agent 指令通道；Agent 输出不可覆盖系统指令；不自动批准工具动作。
- 下一步：Role/Capability Registry、策略约束的工具权限、独立 Reviewer 与验收报告、失败重试/取消/超时、任务图持久化恢复、事件 Hooks，再考虑跨设备 Federation。
- 验收：依赖传递、并行波次、环/未知依赖拒绝、失败阻断、消息审计和旧 fan-out 兼容均有测试。

### B-025 资源感知并行调度

- 状态：CPU/RAM 资源预算与 Worker/Orchestrator 自动并发第一阶段完成；B135 新增跨进程 GPU 独占租约、ComfyUI 释放确认与 Supervisor 显存恢复门槛。真实视频任务中的 GPU 释放/恢复闭环尚待实机验收。
- 已有：`src/muse/resource_governor.py`；`muse-worker --auto-workers --max-workers N`；MultiAgentOrchestrator 默认在每个 fan-out/DAG wave 前重新采样；`src/muse/gpu_resources.py` 使用 SQLite WAL + `BEGIN IMMEDIATE` 提供 GPU lease、heartbeat、TTL 过期回收及事件记录。
- GPU 接线：Wan 生产入口持有独占租约；队列空闲时请求 ComfyUI `/free` 并实测 VRAM；模型 Supervisor 仅让位/终止自己 `Popen` 管理的模型进程，恢复前要求设备显存与 Torch 池均达到 70% 空闲；LM Studio 1234 外部模型在线会触发 `external_model_active`，不启动第二个模型；ComfyUI 在线但显存接口失败则视为未知并 fail-closed。Dashboard 提供 `/api/v1/gpu-resources` 只读状态。
- 默认 CPU/RAM 策略：保留 1 个 CPU 核、2 GiB RAM；每个 Worker 估算 512 MiB；硬上限 32。未知 RAM 保守单并发；预算不足以保留一个 worker 时暂停自动派发。GPU 显存未知不猜测，数据不足时 fail-closed。
- 尚未有：运行中持续 CPU/RAM 降载、任务 CPU/GPU/内存画像、公平队列/背压 UI、按实测任务内存自学习；真实 ComfyUI 卸载/VRAM 回收后再恢复 Muse 模型的完整切换仍待实机验收。
- 验收：原有 CPU/RAM 测试继续保留；B135 GPU 租约/过期恢复、Supervisor 门槛、ComfyUI VRAM 解析/释放、队列保护和外部模型冲突保护专项回归 82/82 通过；全项目 Python 1,164/1,164 通过；Dashboard Node 25/25、Svelte 0 错误/警告、Vite 构建与 Remotion 类型检查通过。当前 MuseLocalModelSupervisor 计划任务 Running，实机检测到 LM Studio 在线时保持 `external_model_active`、Muse 1235 关闭。真实 ComfyUI 卸载与模型恢复仍未验收；不得以压满机器为成功指标。

### B-026 记忆驱动的象征灵感与前瞻引擎

- 状态：多源上下文已接入 AutonomousResearchEngine；量子前沿已有 arXiv + 公共网页双路发现；完整创造力闭环仍在迭代。
- 已有：User/Agent 双轨 MemoryStore；InspirationEngine 融合当前目标/创造者意图、活动 User Track 记忆（优先 8 条主题匹配，无匹配时回退最近 4 条）、显式经验、最多 3 个 thread × 最近 4 条本地对话消息、最多 8 条 ResearchKnowledgeStore 记录；所有上下文携带来源和知识状态。
- 自主研究运行时已注入上述本地上下文；它们只进入本地模型灵感综合，不进入公共搜索 query，也不能作为事实证据或模型指令。真实运行曾读取 User Track 记忆 1 条、知识图谱 8 条、对话 0 条（当时本地对话库没有可读 thread）。
- 象征体系：易经、六爻、黄历／择日、奇门遁甲、紫微斗数、大六壬、西方星相学、吠陀占星术、塔罗、推背图；研究 lens 另含量子理论前沿、阴谋论/争议叙事核查、跨领域结构联想。所有 lens 只产生问题/假设，不是证据或预测。
- 量子前沿使用 arXiv Atom API（按最新提交时间排序）+ DuckDuckGo；来源记录区分 arXiv 预印本与网页来源，日期区分 arXiv 提交日期和出版日期。新鲜度只影响候选排序，不代表可信度。争议叙事使用 2026 传播线索发现 + FactCheck.org 等反证查询，并追溯原始文件、独立核验和替代解释。
- 本地模型集成回归已用隔离测试记忆/对话完成 llama.cpp synthesis 与 claim review；1 条 User Track 记忆、2 条对话和 8 条知识记录均进入 inspiration context，未进入公共搜索 query。新增 creator_inspired_hypotheses 输出，生成最多 3 条引用外部来源、明确 Test/Falsifier 的跨领域假设；真实量子前沿周期已使用 3 个公开来源完成本机模型 synthesis/claim review，并持久化 2 条假设，仍待独立研究。
- 本机模型已恢复：通过 supervisor-owned detached native process 启动 `DeepSeek-V4-Pro-Qwen3.5-9B`（Q6_K + mmproj），`/v1/models` 与 READY 短请求通过。隔离测试来源的本地模型研究周期完成 synthesis/claim review，并持久化 2 条假设（只验证集成）。进一步真实量子前沿周期读取 3 个公开来源、1 个读取失败；本地模型 synthesis/claim review 完成，融合 1 条记忆、2 条对话、8 条知识记录，生成并持久化 2 条具有明确 Test/Falsifier 的假设。来源包含 arXiv 预印本与二手报道，假设仍待独立研究。Detached Job 目前最多运行 3600 秒，长期服务管理/恢复仍待实现。
- 2026-11-08 服务恢复复测：旧 3600 秒 detached job 已结束，旧模型路径已失效；按当前 `D:\\llama.cpp\\start-server.ps1` 实际配置重启 `DeepSeek-V4-Pro-Qwen3.5-9B`。`/v1/models` 与 `/v1/chat/completions` 均真实通过（精确输出 `MUSE_MODEL_ONLINE`）。当前由 Runner-owned interactive process 运行，单次最长 604800 秒（7 天）；仍不是 Windows 永久服务，开机自启、崩溃恢复和长期健康管理未完成。

- 假设持久化：保存外部来源 provenance、内容摘要、上下文来源标签、非证据/非预测状态；建立知识图谱关联，精确重复（忽略 citation ID）复用节点，近似重叠只标记待审。生成阶段强制显式 Test + Falsifier 条款、可测变量和可观察拒绝条件；弱测试描述被拒绝。创造者启发假设进入逐条支持/反证研究队列，有可读来源后仍保持 proposed，需独立复核；候选研究不会递归制造新候选。
- ChatGPT 历史导入已实现用户主动导出后的本地导入：`muse-bridge import-chatgpt-export --input <conversations.json> --confirm-local-import`；只导入可见 user/assistant 对话，排除隐藏 analysis/tool，限流、幂等、默认不覆盖、不联网。仍需账户自动同步/增量更新与隐私治理。
- 尚缺：提高 arXiv 与网页来源可读率；增加 peer-reviewed/实验复现来源与独立来源评估；实时跟踪不同领域的最新研究与争议叙事；语义级跨体系去重、新颖度质量评分、真正独立模型/人工复核、重复实验、桌面 UI；真实术数历算适配器仍未实现。
- 证据边界：个人经验/对话不是外部事实；arXiv 预印本不是同行评审确认；量子术语不得作为宏观因果万能解释；阴谋论/另类说法不预设真假。未经主张级证据审查不得升级事实，任何改进提案仍须安全审查、人工批准、实验与独立 Reviewer。
- 验收：灵感引擎 10/10、arXiv adapter 8/8、ResearchPipeline 23/23、AutonomousResearch 24/24；Python 3.11 全量 unittest **724/724**（56.834 秒），compileall 通过。

### B-027 OMC-inspired 工作流融合（Team / Ralplan / Verify / Skillify / Trace）
- 状態：核心基礎已在程式碼中落地；完整 Commander 計劃共識及端到端階段自動化仍需驗收。
- 已有：`workflow_profiles.py`（版本化 Profile、Ticket 綁定、checkpoint、pause/resume/cancel、CAS revision、lease fencing）；`verification.py`（有界 verify→fix→reverify）；`trace.py`（多假設、事件證據引用、反證檢查）；`skillify.py`（成功運行提取、測試、人工批准）。
- 測試覆蓋：`test_workflow_profiles.py`、`test_verification.py`、`test_trace.py`、`test_skillify.py`；本輪只讀檢視，未重新執行測試。
- 尚缺：Planner/Architect/Critic 真正獨立的計劃審查及修訂流程；上述能力在真實 Commander/Ruflo 執行路徑中的完整串接；完整端到端驗收與 HUD。
- 原則：不得新增第二 scheduler/queue；Reviewer 不可自審；非冪等任務不可自動重試；所有循環具 attempt/time/resource ceiling。
- 詳細映射見 `docs/OMC_FUSION_RESEARCH.md`。

### B-028 ARIS-inspired 自主研究與知識複利（Research Wiki / Meta-Optimize / Experiment Bridge）
- 狀態：A0/A1 完成；A2 Experiment Bridge v1 已落地並接入 TestRunner。全量 Python unittest 511 項：510 通過、1 跳過、0 失敗。核心閉環已驗收；OS 級記憶體硬限制及內建 benchmark 執行器仍是明確後續項。
- A0（已實現）：`src/muse/research_knowledge.py` 使用版本化 SQLite schema 保存 source/idea/experiment/claim/decision/failure typed nodes、supports/refutes/relates/inspired_by/contradicts/supersedes/tested_by/addresses_gap edges、append-only events、provenance、任務引用及失敗經驗 anti-repetition 檢索；新增 `list_edges()`，可按節點/關係讀取有效圖譜邊，`search_context()` 會在文字命中外補入有界一跳鄰居，並標記 `context_origin` / `related_via`。
- A0（已接入）：`ResearchPipeline` 可注入 `ResearchKnowledgeStore`；CLI 預設寫入 `data/research/knowledge.sqlite3`，只登記來源 URL、歸因、內容哈希及抓取元數據，不複製 ResearchEvidenceStore 的正文。知識庫寫入錯誤獨立报告，不中断证据采集。
- A0 護欄：Claim 的 supported/refuted/contested/superseded 狀態只能經 `transition_claim`，必須引用已登記 source/experiment 並提供 reviewer；已知 author_id 時 reviewer 必須不同。relevance_score 僅保留為搜尋線索。
- A1（完成）：ResearchPipeline 與唯一 Commander 在研究/規劃前檢索歷史知識；Commander 將不可信上下文注入 Planner 與 researcher/analyst，現在同時傳遞圖譜鄰居來源及關係方向，供 Agent 區分文字命中與關聯節點。研究後登記 experiment/source/failure；研究與 Commander 失敗均連回所屬 experiment 及 idea，避免負面經驗成為孤立節點。Agent 研究輸出只建立 proposed Claim，不因 Ticket 通過而自動升級為事實。
- A1（完成）：Meta-Optimize v2 擴充 CapabilityRadar，從研究失敗 signature 的跨 Ticket 重複次數及 proposed Claim 積壓量產生去重 ImprovementStore 提案；只讀彙總指標，不複製來源正文/錯誤內容；修改仍須獨立審查及人工批准。
- A1（完成）：Watchdog 以 workflow stage、report、research.knowledge_recorded、review 等事件判斷 15 分鐘實質進展；heartbeat 不再充當進展；停滯只告警/建立提醒，不自動重啟或重派工作，不建立第二 scheduler。
- A2（核心完成）：`ExperimentBridge` 支援 test/benchmark/research/verification manifest、allowlisted argv、相對工作目錄、runtime/memory budget、環境白名單、程式碼 SHA-256、數值結果指標、experiment-run 證據引用及 Claim 關聯。TestRunner CLI 在隔離子程序執行並強制 runtime timeout；成功 run 才能支持 Claim，失敗/超預算 run 可作反證，仍需獨立 reviewer。
- A2 邊界：`memory_budget_mb` 目前是 manifest 中的聲明值，尚未接 OS 級 per-process memory quota；ExperimentBridge 不自行執行 benchmark/GPU/外部資源，只接收既有批准 runner/adapter 的結果。
- 驗收：7 組定向測試 81/81 通過（knowledge 13、pipeline 17、Commander 18、CapabilityRadar 9、Watchdog 11、ExperimentBridge 6、TestRunner 7）；全量 Python unittest 511 項，510 通過、1 跳過、0 失敗；`py -3 -m compileall -q src/muse` 通過；research_pipeline、capability_radar、watchdog、test_runner CLI help smoke 通過，TestRunner 隔離子程序及硬 runtime timeout 有實測。
- 2026-11-04 增量驗收：ResearchKnowledgeStore 14/14、ResearchPipeline 17/17、AutonomousResearch 5/5、Commander Runtime 18/18，合計 54/54 定向測試通過；`py -3.11 -m compileall -q src/muse` 通過。本次未重跑全量 unittest，511 項為前次全量驗收快照，不作本次全量通過聲明。
- 後續項：OS 級 memory quota、專用 benchmark/GPU adapter、HUD 中的知識圖譜可視化；不得以此為由放寬既有人工批准或資源護欄。
- 詳細上游思想、Muse 現況盤點、融合架構及優先級見 `docs/ARIS_FUSION_RESEARCH.md`。


### B-029 閒置自主研究與主動知識提醒
- 狀態：單輪自主研究閉環已實作；本輪補強本機模型輸出恢復與失敗知識回流，並完成一次真實 LM Studio + 公開網搜研究復測。連續定時運行與常駐服務未驗收/未啟動。
- 已有：固定公共技術主題與最近 30 天知識缺口訊號共用輪替器；每輪執行支持/反證查詢，最多讀取 4 個來源、最多 4 個有界 Worker 並受 ResourceGovernor 降載；來源元數據、Claim/Idea 關係寫入 ResearchKnowledgeStore，研究事件寫入 ProgressEventStore，提醒寫入 ReminderStore。
- 模型輸出：統一有界 JSON 解析器接受普通/fenced/preamble/重複 JSON 的首個完整物件；不合併物件、不修補字段，仍執行嚴格 schema 與來源 ID 校驗。LM Studio Claim Reviewer 格式錯誤最多重試一次；自定義注入 Reviewer 不自動重試。
- 失敗學習：synthesis/Claim review 的固定失敗類型會以穩定 signature 寫入失敗知識節點，累積跨研究 run 的 task_ids 並關聯 Idea；重複失敗可進入 `repeated_failure_groups`，觸發後續動態研究課題。原始模型輸出不進失敗節點。
- 真實復測：LM Studio loopback JSON smoke 成功；一次真實研究讀取 3 個公開來源，synthesis=`completed_after_bounded_retry`、Claim review=`completed`；Reviewer 判定 `insufficient`，Claim 保持 `proposed`。本輪主題來源為 `curated_seed`，不是新生成課題。
- 2026-11-09 增量：真實書籤驅動研究連續兩輪曾因 cross-source item 只引用單一來源而 `ValueError`，報告已新增失敗詳情；修正一次重試提示，明確要求一致點/差異項引用至少兩個不同來源，無法支持就回傳空陣列。最新真實研究讀取 2 個來源，模型綜合 `completed_after_bounded_retry`，一致點與差異均引用 `[S1][S2]`；主張審查仍為 `insufficient`。
- Claim 審查結果只作審計；目前研究者與 Reviewer 仍使用同一本機模型配置，不構成獨立模型驗證。來源域名數量不代表來源獨立性或可信度；Claim 不會因格式修復或 Reviewer 回覆自動升級為事實。
- 驗收：自主研究定向測試 **17/17**；Python 3.11 全量 unittest **620/620**；compileall 通過。
- 未有：固定模型 ID/獨立 Reviewer 模型配置、完整來源可信度/來源獨立性評估、從知识缺口自发生成全新研究課題的開放式規劃、桌面/OS 級推送、Windows 常駐服務/開機自啟；部署及自啟須另行明確批准。

### B-055 Initiative-to-prototype 安全本地原型交付
- 狀態：第一階段靜態原型產生器已實作並在真實 initiative 上實測；不執行任意程式碼，尚未達成可執行原型/測試/回滾閉環。
- 已有：`src/muse/prototype_builder.py` 與 `muse-prototype <initiative_id>`；從 `InitiativeStore` 讀取提案，將研究問題、摘要、待驗證假設、反證/限制、設計啟發與創作者興趣渲染成自包含 HTML；Windows 臨時絕對路徑只顯示檔名。
- 安全要求已實作：輸出限定 `data/prototypes/<initiative_id>/<content_hash>/`；UUID ID 校驗、路徑 containment、拒絕 symlink/路徑穿越、HTML escaping、拒絕 script/form/iframe/object/embed/base、事件處理器屬性、外部資源屬性（含 srcset）、CSS url/@import/expression/javascript、meta refresh 與非本地連結、寫新不覆蓋、SHA-256 manifest、builder versioned hash、冪等重跑及修改檔案檢測；只產生靜態 HTML/README/manifest，不呼叫 shell、不聯網、不安裝依賴。
- 真實驗收：initiative `00ec9fb6dda34c7c80334eda97c5ee56` 已產生 `data/prototypes/00ec9fb6dda34c7c80334eda97c5ee56/de0136fc3eb32bbd650c/`（builder v3）；首次 `created=true`、`validated=true`，重跑 `created=false`、同 hash、仍通過驗證；提案狀態進入 `testing`，沒有自動標記 `shipped`。
- 測試：`tests/test_prototype_builder.py` **6/6 通過**，覆蓋 HTML escaping、研究內容解析、Windows 臨時路徑脫敏、冪等、篡改檢測、拒絕無效 ID/已拒絕提案、外部資源、事件處理器屬性、CSS URL/@import、meta refresh 和 srcset。
- B-058 已完成持久化反馈记录；B-059 已将 revise/reject 反馈幂等导入 `ImprovementStore` 的 proposed 队列，保留原文和审计元数据并标注为未验证信号。

### B-060 评审反馈驱动的自主研究
- 状态：研究桥接已实现；反馈提案进入自主研究优先队列。
- 已有：只对本地 `initiative_review_feedback` 提案生成研究课题；公开检索词为固定通用词，不包含反馈原文、评审者或 initiative ID；本地 idea/experiment 以 proposal_id、feedback_id 关联。
- 已有：自动建立支持/反证研究计划、可复现验收标准与失败处理策略；检索/读取失败按脱敏阶段签名保存到 knowledge failure 节点。研究计划不等于已执行实验。
- 安全边界：反馈仍是未验证信号，不自动批准、实现或发布。

### B-061 反馈研究提案生命周期与审查交接
- 状态：研究生命周期第一阶段已实现。
- 生命周期：`proposed` → `researching` → `ready_for_review`。证据不足保持 `researching` 并优先重试；来源按 proposal+URL 幂等存储。
- 进入 `ready_for_review` 的最低门槛：至少 2 个可读来源、支持和反证方向都有来源、至少 2 个不同 URL、每个来源有 SHA-256 且正文不少于 200 字符。
- 所有自动导入来源默认 `unknown` license / `needs_review` security；显式人工来源审查后才能满足 `approve()` 条件。研究状态交接不等于事实认定或代码审批。
- 复用既有 `ExperimentBridge`、`TestRunner`、`IndependentReviewGate` 与 `ApprovedCodeChangeExecutor`，不重复造执行/审查/回滚组件。
- 未有：反馈驱动实验计划的自动执行器；独立 Reviewer 模型身份/配置与真实部署验收；任何代码修改仍要求显式人类批准及现有 Ticket 审批记录。

### B-030 Commander 单次规划与预授权
- 已完成：Commander 初始计划只生成一次；角色能力预检发生在 Ruflo Agent 启动前；Critic 审查预校验计划，只有拒绝且提供修订反馈时才进行一次有界重规划。- 已加固：模型生成的错误依赖容器/引用会进入有界结构修复，仍不合格则 safe fallback；并行角色契约校验不再对不可信类型直接执行 `set()`，避免意外 TypeError。
- 已验证：Consensus 重用候选计划、拒绝目标不匹配计划、角色权限失败时 Ruflo 零调用；Planner/Runtime + DAG + Research 定向测试 **117 项通过**，compileall 通过。
- 未验收：真实 LM Studio + Ruflo 多 Agent Ticket 的完整执行；本轮未启动真实执行，需在受控本机入口完成只读实跑。
- 后续：完整多 Agent 成功与失败恢复验收、Reviewer 的真实独立性验证；不因 Critic 通过而放松能力白名单。
### B-031 一键 AI 影视工厂（Muse + ComfyUI + Velorn + FFmpeg）
- 状态：素材自动合成、Remotion 品牌片头/片尾及逐场景 TransitionSeries 已接入 VideoProductBuilder，并由真实 VideoProductBuilder 路径完成混合素材渲染验收；支持隔离的 motion_workspace。RX 6750 GRE 12GB + ComfyUI 0.23.0 + Wan 2.1 T2V 1.3B 已完成真实 GPU 生成并交付通过 Muse QA 的 MP4/ZIP。Velorn v0.3.37 本机 MCP 已实测。
- 主方案：Muse Commander 唯一调度；ComfyUI 生成；Velorn 提供生成工作台、真实时间线和本地 MCP；FFmpeg/FFprobe 负责确定性合成、编码和校验。研究见 docs/VIDEO_FUSION_RESEARCH.md。
- 已有：src/muse/video_factory.py + muse-video make，混合图片/视频自然顺序组装、统一画幅/FPS、可选 BGM/旁白、AMF/x264；FFprobe 校验视频流、时长、目标画幅/FPS，以及请求的音轨是否存在。
- ComfyUIClient 支持节点摘要、history、queue_status 与审批+节点白名单保护的 workflow submit；POST 前还会从 /object_info 确认每个 class_type 已安装；任何未批准/含白名单外/缺失节点的图都不会 POST。
- 复用增强 src/muse/video_workflow.py：scene depends_on、未知/自依赖/循环检测、DAG 并行 waves、CommanderPlan 映射与现有 plan CLI；不引入重复 schema。
- 测试：B131 混合 2 张图片 + 1 段视频的 1920×1080 / 30 FPS Remotion 场景时间线、音轨保留、片头片尾、完整解码、Muse QA、ZIP CRC 与 SHA-256 实机 smoke 通过；Python 全量 unittest 1,140/1,140、compileall 通过；Dashboard 25/25、Svelte check 0 errors / 0 warnings、Vite build 与 Remotion TypeScript check 通过。VideoProductBuilder v5 统一使用最终 MP4 元数据，避免把 AI 原始镜头时长误报为成片时长。
- 仍未有：长片/高分辨率 Wan 生成分级验收、故事自动生成与角色一致性、自动字幕/配音、Velorn 时间线写入/导出实测、完整镜头质量 Review、多平台发布与收益复盘闭环。
- GPU 路线：ComfyUI 0.23.0 + Torch 2.10.0+rocm7.14.0a20260603 / HIP 7.14.60850 真实识别 RX 6750 GRE 12GB，Wan 节点与模型齐备；真实 Wan 1 秒/8 steps/512×288 T2V 生成成功，Remotion 包装后 4.42 秒 MP4，Muse QA、全片解码、ZIP CRC、SHA-256、verify_path 全通过。OSError 1455 的直接诱因是 llama-server 占用约 19.4 GB 提交内存；精确停止 model_supervisor/llama-server 后 Wan 成功。D:\\pagefile.sys 设置已调至 64 GB 初始 / 96 GB 最大，当前分配约 32.7 GB。
- ComfyUI 目前保持在线、GPU 约占用 9.9/12 GB；恢复 llama.cpp 9B 服务时 Vulkan 因显存不足约 918 MB 分配失败，本地 LLM 暂时离线。下一步先实现 Muse GPU 资源仲裁，避免 ComfyUI 与 llama.cpp 同时抢占显存/提交内存；按需启动、暂停并恢复服务。

### B-039 增量：前沿课题检索模板
- 已有：自主研究课题池增加量子理论前沿和近期阴谋/争议叙事核查主题；每个主题都必须执行支持与反证查询，量子查询区分同行评审/预印本/实验，争议叙事查询追踪原始材料与独立 fact-check。
- 验收：新增课题契约测试；灵感 9/9、自主研究 23/23；Python 3.11 全量 unittest 710/710，compileall 通过。
- 限制：还未运行这两个新主题的真实公开网搜周期；当前没有可声称为最新的具体理论或阴谋叙事结论。

## 全域完成定義
- 有可重現測試與真實執行證據。
- 記錄在 docs/PROGRESS.md，更新本 backlog 狀態。
- 有失敗處理、超時、取消或回退路徑。
- 不突破免費額度、私隱、安裝、服務部署及人工批准邊界。

### B-062 反馈研究计划的白名单实验执行接线
- 状态：第一阶段已实现并完成定向/端到端测试；当前仅执行固定的 Muse 研究生命周期回归套件，不是通用科学实验执行器。
- 已有：`run_feedback_research_experiment()` 只接受代码内固定 key `feedback_research_lifecycle`，绑定 `tests/test_autonomous_research.py`；不接收用户/模型提供的 argv、路径或命令。
- 已有：执行前校验 proposal_id、feedback_id、plan_node_id 精确关联；只复制 `src/` 与 `tests/` 到临时快照；不复制 data/config/私密记忆，不修改原工作区。
- 已有：复用 ExperimentBridge + 隔离 TestRunner，120 秒 runtime ceiling、2048 MiB Windows Job Object memory limit、SHA-256 manifest、固定测试指标与 ProgressEvent；稳定 run_id 按提案/反馈/计划和代码指纹生成，重复调用返回既有 run。
- 已有：运行结果与计划、提案、反馈关联；失败写入 Failure 节点并明确不是 Claim 反证；成功/失败都不自动升级 Claim、不批准提案、不执行代码变更。
- 完整性加固：`ExperimentBridge.record_result()` 对稳定 `run_id` 实施结果不可变规则；完全相同的终态重放返回原结果且不重复追加知识事件，任何状态、摘要、指标、环境/代码指纹或原始证据哈希冲突均拒绝覆盖，避免重试或重复投递改写既有实验结论。详见 `docs/PROGRESS_20261115_EXPERIMENT_LEDGER.md`。
- 崩溃恢复补强：完全相同的结果重放会幂等补齐缺失的 manifest `tested_by` 边；若边已存在，不重复追加 edge event。结果节点仍不可变，冲突重放仍拒绝。专项测试已覆盖“节点已保存、关系尚未写入即中断”的恢复窗口。
- 边界：独立 Reviewer 仍待真实不同身份/Provider 配置；工程回归只验证 Muse 软件流程，不是外部事实证据。Windows Job Object 资源限制不等于 OS 网络隔离或完整 sandbox。
- 验收：反馈实验专项 4/4、自主研究 39/39、进化执行器 12/12、TestRunner 7/7；本轮 ExperimentBridge/反馈实验/自主研究/知识图谱定向回归 **51/51**；Python 3.11 `compileall -q src tests` 通过；全项目 unittest **1,221 项运行，1,220 通过、1 项可选依赖跳过，0 failures / 0 errors**（127.485 秒）。non-Git source freshness 仍为 `unproven`，不宣称机器审计全绿。
### B-063 可选独立 Reviewer 通道
- 状态：**反馈实验真实模型批准/拒绝双分支、拒绝 Ticket、Worker handoff 与后续研究周期均已验证**；Builder/Provider 独立性仍 pending。
- 配置：`MUSE_REVIEWER_MODEL` 必须与 `[models].model` 不同；可选 `MUSE_REVIEWER_URL` 必须为 loopback HTTP；启动前确认 reviewer model 在本地 `/v1/models` 中存在。
- 2026-11-19 实测：Builder 当前配置 `DeepSeek-V4-Pro-Qwen3.5-9B` 指向 `127.0.0.1:1235/v1`，该端点当前不可达；`127.0.0.1:1234/v1` 可达并暴露 5 个模型，其中 Gemma 4 E4B 候选可真实响应。
- 通过显式 Reviewer URL/Model 环境变量，实际装配 `AutonomousSupervisor._build_feedback_reviewer()`，调用真实 Gemma 模型，并经 `IndependentReviewGate.review()` 完成结构化审查；临时 SQLite ReviewStore 写入后重新读取成功。合成样本只证明端到端调用、协议解析与持久化链路可用，不代表生产代码审查质量。
- 第一轮模型把 findings 返回为字符串数组，Gate 正确 fail-closed；已收紧 system prompt，要求每条 finding 必须为 `severity/title/detail` 对象。新增 timeout 与错误 findings schema 测试。
- 任何审查结果都不等于外部 Claim 证据，不升级 Claim/Improvement，不批准或执行代码变更。源码审查通过不代表生产拒绝回路或 Provider 独立性已完成。
- 验收：B-063 focused regression **18/18**、`test_worker.py` **32/32**、`test_autonomous_runtime.py` **19/19**；`compileall -q src tests` 通过；全仓 unittest **1,563 项运行、1,554 通过、9 项跳过、0 失败/错误**。详见 `docs/PROGRESS_20261119_B063_REJECTION_FEEDBACK_LOOP.md`。
- 2026-11-19 真实源码差异审查：Reviewer `local-reviewer:011d7c5831f7d581e81cce35`，Review ID `ecc33bb2e31f47d58f296801bff2f90b`，approved=true、4 findings（含 1 条 medium）；只覆盖所提交的两个文件 diff，不覆盖整个 workspace。详见 `docs/PROGRESS_20261119_B063_REJECTION_FEEDBACK_LOOP.md`。
- 最新源码复审：Reviewer `local-reviewer:011d7c5831f7d581e81cce35`，Review ID `b3add74f52b64c87b14270aa99bb173c`，approved=true、1 条 info finding；ReviewStore 读回成功。
- 真实模型拒绝 smoke：合成 SQL 注入样本获 `approved=false`，包含 critical/high findings，临时 ReviewStore 读回成功；未写入生产库。
- 最新真实闭环验收（2026-11-19）：正向真实 Gemma 审查 56 项反馈实验回归并批准；负向隔离副本 57 项测试中 1 项受控失败，Gemma 实际拒绝，ReviewStore 持久化并生成唯一 read_only_research follow-up Ticket；随后 AutonomousResearchEngine.run_once() 实际消费拒绝主题，支持/反证方向各入库 1 条，Claim 保持 hypothesis-only，下一轮 query-pair 去重通过。全部使用临时数据库/副本，未写生产库。
- 当前 B-063 状态：真实模型批准/拒绝双分支、拒绝 Ticket、Worker handoff、不注入 preferred topic key 的自主研究周期，以及 handoff 后下一 tick 唤醒 Routine 均已验证。最新 Python 3.11 compileall 通过；全仓 unittest **1,616 tests，1 skipped，0 failures / 0 errors**。Builder 1235 无监听，Provider/进程独立性仍 pending；FFmpeg moov 警告待定位。详见 docs/PROGRESS_20261119_B063_REJECTION_FEEDBACK_LOOP.md。

### B-064 证据覆盖与来源质量画像
- 状态：已实现并通过专项与完整回归。
- Claim dossier 统计支持/反证/上下文覆盖、已审/未审证据、最新 verdict、质量/独立性等级、reviewer 数量与 verdict 冲突。- 汇总 SHA-256、正文长度、HTTPS、发布日期状态、hostname/启发式 registrable-domain，以及经过审查的来源依赖/独立关系。
- 自主研究 JSON/Markdown 报告直接呈现 coverage 与 lineage；不自动生成信誉分/置信度总分，不把检索方向当证据 verdict，不把域名分组当所有权证明，不改变 Claim 状态。
- 验收：`test_research_knowledge.py` **30/30**、`test_autonomous_research.py` **39/39**；Python 3.11 compileall 通过；完整 unittest **816 项，0 failures、0 errors**（65.179 秒）。

### B-065 事实级引用索引与报告整理
- 状态：已实现并通过专项与完整回归。
- 对通过 schema 校验的事实性综合陈述逐条映射 source ID、title、URL、citation_id、SHA-256；覆盖 executive summary、一致点、差异、反证、设计启发和实验条目。
- 灵感假设单独处理，不当作事实；引用矩阵标注需人工核对，不声称自动验证来源是否真正支持陈述。
- 修复 Markdown 报告来源元数据行拼接及 ResearchPipeline 重复 source URL 映射赋值。
- 验收：`test_autonomous_research.py` **40/40**、`test_research_pipeline.py` **28/28**；compileall 通过；完整 unittest **817 项，0 failures、0 errors**（65.031 秒）。

### B-066 Research Wiki → Obsidian 安全镜像（第一阶段）
- 状态：显式选择节点的单向 create-only export 已完成并通过完整回归。
- CLI：`muse-memory export-research-wiki --knowledge-db data/research/knowledge.sqlite3 --vault <existing-vault> --node-id <id>`；每个节点都必须显式传入 `--node-id`。
- 输出仅含节点标题/摘要/状态/时间戳/provenance/external ref 与所选节点之间的关系；不导出任意 metadata 或来源正文；SQLite 仍是权威源。
- 已存在文件不覆盖、不删除；仅导出活动节点，Vault 必须已存在；proposed/hypothesis/planned 等状态原样保留。
- 验收：`test_obsidian_vault.py` **16/16**；compileall 通过；完整 unittest **820 项，0 failures、0 errors**（66.468 秒）。
- 未完成：逐笔 diff UI、人工批准回流、冲突解决、双向同步与回滚。
 
### B-067 Research Wiki 镜像只读审计与哈希基线
- 状态：已实现并通过专项与完整回归。
- 新导出笔记保存正文 SHA-256；旧笔记无哈希时标记 legacy_unverifiable，不修改旧文件。
- 新增 muse-memory audit-research-wiki --knowledge-db <db> --vault <existing-vault>，只读扫描 05-Research-Wiki/，检查 node identity/path、SQLite 更新时间与状态、正文哈希。
- 分类：clean、external_edit、stale_source、orphaned、malformed、legacy_unverifiable；不托管的个人笔记忽略；报告明确 writes/deletes/automatic_repairs=0。
- 测试：Research Wiki audit 新增 5 项；test_obsidian_vault.py 21/21；Python 3.11 compileall 通过；最终完整 unittest 825 项，0 failures、0 errors（68.246 秒）；超过 500 个活动节点时对未命中缓存的 ID 精确回查，避免误报 orphan。
- 边界：没有对真实用户 Vault 执行扫描；尚未实现逐笔 diff UI、人工编辑批准回流、冲突解决、回滚与双向同步。
 
### B-068 原始来源快照完整性与跨来源差异候选
- 状态：已实现并通过完整回归。
- ResearchEvidenceStore.audit_integrity() 重算已保存来源正文 SHA-256；audit_database() 使用 SQLite mode=ro，仅审计既有 DB，不返回正文、不写入/修复；缺失 DB 不创建。
- CLI：muse-memory audit-research-evidence --evidence-db <existing-db> --limit <1..100>。
- 自主研究在 synthesis/review 前核验来源 text SHA-256 与 citation_id；任一来源快照无效时停止事实综合和主张审查。
- 至少双来源引用的 model differences 生成待审差异候选，持久化为 proposed Idea；与来源/父 Claim 仅用 relates 边，不建立 contradicts，不确认事实，不改变 Claim 状态。
- 专项：test_research_fetch.py 13/13；test_autonomous_research.py 44/44；compileall 与 CLI help 通过；完整 unittest 832 项，0 failures、0 errors（67.828 秒）。
- 边界：未审计真实用户 evidence DB；差异候选仍须人工比较原文，不能把模型综合当成矛盾事实。
 
### B-069 反证、替代解释与失败经验图谱化
- 状态：已实现并通过完整回归。
- ResearchKnowledgeStore schema v5 新增 alternative edge；v1–v4 迁移保留原有 knowledge_edges 行、ID、metadata 与时间戳。
- 有引用的 counter-evidence 保存为 proposed Idea，source/idea/claim 只用 relates 边；替代解释各自保存为 proposed Idea，通过 alternative 边关联差异候选；不自动生成 refutes/contradicts 结论。
- 失败经验沿用 ResearchPipeline → Failure 节点 → Experiment/Idea 的持久化链路；研究报告展示 failure IDs。
- 测试：test_research_knowledge.py 31/31；test_autonomous_research.py 44/44；compileall 通过；完整 unittest 833 项，0 failures、0 errors（69.837 秒）。
- 边界：真实用户 knowledge.sqlite3 尚未迁移；反证和替代解释都需人工核对原文，不能自动裁定。

### B-085 研究灵感到离线产品的自动生成与交付
- 状态：第一条安全纵向切片已完成；产品专用模板与发布审批仍待扩展。
- 已有：AutonomousRuntime 在自主研究后自动把 Initiative 交给 LocalProductBuilder；离线可信模板生成 HTML/README/manifest/ZIP；产品哈希校验、幂等、拒绝覆盖篡改文件；Dashboard 产品库支持预览和下载。
- 安全边界：只执行固定审查模板，不执行模型生成代码，不联网，不自动升级 testing 为 shipped，不自动修改 Muse 源代码。
- 验收：产品生成 4/4、研究 44/44、Status API 15/15、Python 全量 932/932、UI API 14/14、Svelte-check 0 errors、Vite build 成功。
- 后续：按 Initiative 类型继续扩展模板；补产品人工验收/反馈回流/版本发布。B-086 已将本地素材视频剪辑作为 `video_creator` 产品类型接入；prompt-to-video 与 AI 镜头生成仍受模型/工作流未就绪限制，不改变自主研究优先级。
### B-086 视频创作产品模板与本地硬件编码交付
- 状态：本地素材剪辑/合成已实机验收；Wan 2.1 1.3B prompt-to-video 工作流与产品交付代码已集成，真实 GPU MP4 验收待 Windows 页面文件扩容重启后完成。
- 本地素材模式：图片/视频素材 → FFmpeg → FFprobe → manifest/ZIP；支持 AMD AMF、软件编码、背景音乐和旁白。
- AI 模式：新增固定 Wan 2.1 T2V 1.3B 图；Dashboard 可输入 prompt/negative prompt、选择横/竖/方画幅与 1–5 秒预览；异步任务完成后自动生成版本化 `video_creator` 产品并提供 MP4/ZIP 下载。
- 安全：ComfyUI 仅 loopback；工作流节点固定 allowlist；输出文件名/类型/MP4 签名和大小受限；不执行任意节点、不调用云端 API、不自动发布产品。
- 模型：三个 Wan 模型资产已安装并 SHA-256 校验；ROCm 7.14 已识别 RX 6750 GRE 12GB。真实加载 UMT5 曾因 Windows 页面文件仅 2GB 报 1455；已配置 D 盘页面文件 16–32GB，需重启后生效。
- 验收：Wan workflow 5/5、video product builder 6/6、video product API 4/4、engine safety 22/22；Python 全量 948/948，0 failures / 0 errors；compileall、Svelte-check（0 errors/0 warnings）与 Vite build 通过。**注意：948/948 是自动化测试，不代表真实 AI MP4 已生成。**
- 下一步：Windows 重启使页面文件设置生效；启动 ComfyUI，真实生成 1–5 秒 MP4，检查 VRAM/系统内存、视频时长/分辨率/帧率、产品 ZIP 与 Dashboard 预览下载；再逐步扩展多镜头、图生视频、音频/字幕与更高分辨率。
## 主路线索引
- `docs/MASTER_ROADMAP.md` 汇总此前已确认的所有未完成计划，按 P0 研究实验闭环 → P1 自主研究 → P2 Research Wiki/Obsidian → P3 自我进化 → P4 后台恢复/主动汇报 → P5 Commander/Agent → P6 Rust 桌面控制台 → P7 Runner/可选本地模型 → P8 视频工厂（暂缓）排序。
- 当前主线不得被视频工厂、ACP Provider 或纯 UI 工作抢占；阶段验收必须有真实执行、持久化结果、失败/回退路径和测试证据。
### B-100 REA + Craft Anything + ego-lite 软件研究、网页研究与创造引擎
- 状态：核心代码接线与定向测试通过；真实浏览器 app、Craft Study/Build/Check 与产品交付仍为 partial，不标记完工。
- REA 6.5.0：MCP initialize/tools/list 与只读工具调用通过；Muse 自有 UI 原型已由 REA 官方 CLI 完成真实 JavaScript 应用分析。证据目录 `data/software_crafts/b100-ui-prototype/rea-js-summary-5/`，含 36 MB evidence JSON 与独立 SHA-256 manifest。
- Craft Anything v0.6.1：官方 Windows x64 ZIP digest/CRC 与安装 CLI Authenticode 核验通过；79 MCP tools、20 read-only；`core_session_show` 实测成功。website Study 被 Craft 自身 `consent_required` 阻挡，未绕过；Build/Check E2E 待完成。
- 本地 Builder：已修复 `config/muse.toml` 的 `[models]` / 旧 `[model]` 兼容问题；Craft `setup` 已把 `Muse Local` 设为 builder，llama.cpp `DeepSeek-V4-Pro-Qwen3.5-9B` loopback 连通性测试通过（11.28 s）。
- ego-lite：源码 v2.2.0 commit `bacaece43900e8ca68eea3b6f1663835ea38df9e` 已固定；桌面 app v0.6.2.10 已通过官方 WinGet 包安装，安装器 SHA-256/Authenticode 均验证通过。实际为 system-level 安装；Muse adapter 可在 PATH 未刷新时发现标准安装目录的 CLI，Doctor 报告 `installed_unverified`；Runner 启动尝试后 app 进程立即退出，交互式 onboarding/runtime probe 待验收。未导入浏览器数据。Muse 的三个 adapter 已接入既有研究流水线。
- 研究能力：公共 Google 搜索只接收当前课题的 curated support/counter queries；HTTP reader 优先，ego-lite 仅作为动态/短内容 fallback；保留截图、元数据、链接、素材清单与证据路径。
- 素材策略：只自动收集同源公开图片/PDF，最多每页 3 个、每个 2 MiB、总计 5 MiB；HEAD 预检、manual redirect、CORS 尊重、SHA-256；license_status=unreviewed、reuse_allowed=false，禁止自动发布/转售。
- 架构：REA=分析证据；ego-lite=公共网页搜寻/动态观察/有限素材收集；Craft=已授权 Study/Check/构建能力；Muse Commander 仍是唯一调度中心，复用既有 TaskQueue/Worker/Approval/TestRunner/ReviewGate/ProductBuilder。
- 去重：移除重复 MCP stdio session 与未接入 Worker 的重复 DAG enqueue helper；保留现有 `MCPStdioClient`、`SoftwareCraftingAdapter.enqueue_task` 和 plan-only `muse-recreate`。
- 定向测试：`test_ego_browser.py` 19/19、`test_browser_research.py` 8/8、`test_software_crafting.py` 32/32 通过；Python 3.11 全量回归 1,046/1,046，0 failures / 0 errors，105.920 秒；compileall 通过。
- 下一步：首次启动 ego-lite 并完成 onboarding（是否导入浏览器数据由用户决定）；随后验证 runtime probe、Google 搜索、Muse UI 动态页面捕获与真实素材下载；再解决 Craft consent，执行自有测试目标的 Build→Check→Review→Package 纵向验收。
- 额外阻塞：创建 Evidence Explorer smoke product 的 TaskQueue enqueue 被 Runner 安全检查在启动前拦截；没有直接写数据库或绕过 Commander。需恢复该明确 enqueue 操作后才能进入 Build→Check。

### B-111 Initiative Ledger：创作生命周期与作品交付追踪
- 状态：核心持久化、LocalProductBuilder、只读 timeline API 与 Dashboard 时间线第一阶段完成；Research Wiki ↔ 视频产品、创作反馈/视频失败 → Wiki/Capability Radar 的后端纵向切片均已实装并通过全量回归。Dashboard 反馈提交 UI、真实 GPU 失败/成功观测仍待实机验收。
- 已有：InitiativeStore 新增 append-only `initiative_events`，记录创建、状态变化、反馈和产品构建；业务状态与事件在同一 SQLite transaction 写入；UPDATE/DELETE 由 SQLite trigger 拒绝。
- 已有：LocalProductBuilder 在离线产品验证后记录相对路径、SHA-256 与 validated=true；按产品哈希幂等去重；只记录 testing，不自动发布；Windows 路径安全规范化。
- 隐私/迁移：事件不复制反馈正文；旧 Initiative 不回填虚构历史；事件 details 限 4 KiB；旧数据库通过 CREATE TABLE IF NOT EXISTS 增量升级。
- 验收：InitiativeStore 6/6、LocalProductBuilder 4/4、当前/legacy timeline API 定向测试 2/2；Python 3.11 全量 unittest 1,069/1,069；compileall 通过。non-Git source review 不可用。
- 已新增只读 `GET /api/v1/initiatives/{id}/timeline`，使用 SQLite mode=ro，不会创建或迁移旧数据库；仅输出 allowlist 事件字段，并通过 `history_complete` 标明是否有真实 created 事件。
- Dashboard 产品库已增加按需展开的“创作时间线”，展示创意创建、阶段变化、评审决策、产品路径与哈希摘要；反馈正文不外露。
- 前端验收：Node API tests 16/16、Svelte-check 0 errors / 0 warnings、Vite build 成功；后端当前/legacy timeline 定向测试通过。
- 已补充 B-117：VideoProductBuilder 在既有知识库存在时，将验证通过的视频 artifact 以 source 节点登记，保存 MP4/ZIP/Manifest/素材哈希与 validation；通过既有 link_node_to_initiative 与 Creator Initiative 互联，并按精确 Evolution Cycle 关联同周期 source/idea/experiment/claim/decision/failure。识别既有链接避免重复追加，保留原语义关系；查询上限 500，触顶显式标记 bounded。
- 边界：无 Cycle 时只关联 artifact/feedback/failure 本身，不猜测所属周期；知识库缺失时不创建数据库；反馈进入 Wiki 的 idea 节点并标记 unverified_user_feedback_signal，失败进入 failure 节点并标记 observed_execution_failure_not_claim_evidence；不改变 Claim 事实状态、不自动批准改进或发布产品。
- B-117 验收：视频/知识/统一 Initiative/ego-browser 定向测试 71/71；Python 3.11 全量 unittest 1,090/1,090；compileall 通过。
- B-118 创作反馈回流：新增带可选 Cycle ID 的 `initiative_feedback` 迁移；loopback `POST /api/v1/initiatives/{id}/feedback` 可写入审片反馈；同步器幂等生成待研究 Improvement Proposal、结构化 allowlist feedback label 与 Research Wiki idea 节点，并链接 Initiative/显式 Cycle；Capability Radar 可据此生成 `user_feedback` 提案。`accept` 只记录信号，不产生负向提案；`revise/reject` 保持 proposed 且要求独立研究/审核。
- B-119 视频失败闭环：新增 append-only `initiative_failures`；视频任务失败时只保存稳定 `video.product_job_failed` 代码、异常类型与显式 Cycle，不保存原始异常文本；时间线新增 `product_failed`；同步器将失败登记为 Research Wiki failure 节点并关联 Initiative/Cycle；Capability Radar 仅在 24h lookback 内同类视频失败达到 2 次时生成 `creator_video_failure` 改进提案。
- B-118/B-119 验收：反馈桥接/迁移/雷达/状态 API/自主研究定向组合 111/111；Python 3.11 全量 unittest 1,098/1,098，0 failures / 0 errors；compileall 通过。GPU 真实生成未在本轮启动，失败路径使用故障注入验证；Dashboard 反馈提交控件仍待接入。
- B-120 Dashboard 审片与交付指标第一阶段：产品卡片可展开 Creator Initiative 时间线，并提交 accept/revise/reject + 最多 8,000 字审片意见；API 通过本机 Bearer/Origin 防护写入既有 Initiative 账本，下一轮 Autonomous Research 幂等同步到 Research Wiki/Capability Radar。产品列表只对确有 Creator Initiative 关联的产品开放审片入口。
- B-120 交付可观测性：`build_products` 只展示通过 MP4/ZIP/Manifest 哈希与 ZIP 内容验证的作品；新增受限 `video_metrics`（时长、FPS、帧数、分辨率、编码器/模型/seed/steps/aspect ratio）与 MP4/ZIP SHA-256，Dashboard 展示实际 Manifest 数据，不填充估算值。
- B-120 验收：Python 视频 Dashboard + Status API 定向 24/24；Python 3.11 全量 unittest 1,099/1,099；Node API tests 18/18；Svelte-check 0 errors / 0 warnings；Vite production build 成功；compileall 通过。真实 GPU 成片未在本轮生成，交付指标来自可验证 Manifest fixture 与现有产品读取路径。
- 下一阶段 B-121：跨产品交付指标聚合（区分 verified products 与 failed attempts，样本不足时不计算成功率）、审片反馈评分/标签趋势；真实 ComfyUI supervisor 生命周期与 CLIP 重载崩溃实机修复；真实 Wan 2.1 MP4/ZIP 下载和播放验收。
### B-121 Video Attempt Ledger 与交付产能对账
- 状态：第一阶段完成（SQLite attempt ledger、视频 Worker 生命周期接线、只读聚合 API）；Dashboard 面板和真实 ComfyUI 实机验证待完成。
- 已有：每次视频任务以 job/attempt ID 登记 queued/running/succeeded/failed；成功绑定经产品校验的产品/版本及 MP4/ZIP SHA-256；失败保存稳定 failure code 与 error type。attempt events append-only，终态重复请求幂等，冲突终态拒绝覆盖。
- 已有：GET /api/v1/video-attempts 返回脱敏 attempts 与聚合计数；成功率分母严格为 succeeded + failed，pending 单独计数，无终结样本时不计算成功率。
- 验收：attempt ledger + Dashboard API 定向测试 6/6；Python 3.11 全量 unittest 1,103/1,103；compileall 通过。
- 未有：Dashboard 可视化、Cycle 维度产能趋势、真实 GPU 任务全链路回归、ComfyUI 长驻 supervisor 与 CLIP 重载崩溃修复。
- B-121 Dashboard 面板接线完成：显示已验证交付、失败、pending、终态样本及分子/分母；Node API tests 19/19、Svelte-check 0/0、Vite build 通过。真实浏览器交互/桌面服务重启仍待验收。

### B-122 Ploid 专项人物研究 Provider
- 状态：本地 stdio adapter core 与离线测试完成；Ploid workspace 登录、真实 server command 配置、Commander 注册及实机调用未完成。
- 已有：`src/muse/ploid_adapter.py` 使用 `PloidStdioTransport` 复用 Muse `MCPStdioClient`，每次调用动态刷新 tools/list；支持人物候选搜索、单人研究、People Sets、决策者搜索、run_id 持久化、至少 5 秒轮询间隔与显式取消门槛。
- 成本保护：搜索至少预留 1 ACU；单人研究至少预留 25 ACU；People Set 按请求人数每 10 人 1 ACU 向上取整；付费方法必须显式 budget_authorized 与 max_acu，通过预算门槛后才允许 MCP client 执行付费工具。
- Research Wiki：只导入已完成且身份已解析的结果；人物研究存为 proposed idea，来源存为 source，关系只用 relates；保留 provenance/limitations/Cycle ID，并标记第三方结果尚未由 Muse 独立核验。只保存公开专业字段，不保存 email/phone。
- 用户授权：新增 `scripts/ploid-login.ps1`，仅运行官方 `npx @ploid/mcp login` 设备授权流程；本轮未执行登录、未保存凭证、未消耗 ACU。准确的本地 MCP server 启动命令由配置提供，不在代码中猜测。
- 限制：Ploid 是专业人物情报服务，不是通用学术/技术深度研究引擎。Hosted MCP 使用 Streamable HTTP + OAuth 2.1；本轮走本地 stdio 方案，尚未注册到 Commander。
- 验收：adapter 测试 8/8；视频账本 + Dashboard integration + video product API 定向组合 21/21；项目 `.venv` Python 3.11 全量 unittest 1,114/1,114，0 failures / 0 errors；compileall 通过。Dashboard Svelte-check 0 errors/warnings，Node tests 19/19，Vite build 通过。
- 下一步：用户完成一次 Ploid device login 并提供/确认本地 MCP server command；接入 Commander 的可选人物研究能力；在用户确认 ACU 预算后做一次真实端到端研究。

### B-129+ 用户要求：逐项功能交付、图文教学与真实验收
- 决策：Muse 的所有功能必须逐一介绍、逐一实现、逐一教学、逐一验收；每项都说明用途、真实完成状态、前置条件、操作步骤、预期结果、依赖/成本/权限边界，并提供真实截图或明确标注的示意图。
- 交付标准：真实 API + 持久化 + UI + 失败路径 + 可复现测试 + 用户手册；按钮/静态图/构建通过均不单独构成功能完成证据。
- 工作节奏：一个模块一个垂直切片，盘点→设计→TDD→实现→图文教学→测试→实机验收→记忆/进度记录，再进入下一项。
- 本地长期记忆：将此作为 Muse 项目长期产品偏好，写入 User Track；工程方法、实现细节与测试证据写入 Agent Track/项目文档。用户事实与 Agent 推断严格分轨。
- 执行依据：`docs/MUSE_FEATURE_DELIVERY_DOCTRINE.md`；B129 用户手册为当前桌面功能现状基线。

### B-136 Infinite Creation 选择性超融合与商业化视频闭环
- 2026-11-14 首条原创漫剧试产《纸鹤渡命人》第一集完成：29.5 秒 9:16，3 段本轮 Wan 2.1 新镜头 + 6 段既有镜头；粤语旁白稿已写，尚无可用粤语 TTS。Muse 产品登记并关联 Research Wiki；最终 v4 技术 QA 0 warnings、单视频流、完整解码通过。未发布、无收入。详见 `docs/PAPERCRANE_MANGA_PILOT_20261114.md`。
- 2026-11-14 首條商業試產已真實產出：24.5 秒廣東話字幕 9:16 短片；第二版加入 FFmpeg Ken Burns 推進及字幕浮動，技術 QA 0 warnings、完整解碼通過。詳見 `docs/COMMERCIAL_VIDEO_PILOT_20261114.md`。本輪重用既有 Wan 鏡頭，未重新運行 ComfyUI；本機粵語 TTS 缺失、未發布、無收入。下一步優先補 shot-level source provenance、粵語 TTS 實機驗證及發布前人工審片。
- 状态：IC-M0 研究完成；尚未 clone/安装/运行上游。研究报告：`docs/INFINITE_CREATION_FUSION_RESEARCH.md`。
- 决策：不整仓并行部署；Muse Commander、TaskQueue、GPU lease、VideoProductBuilder、Remotion、QA、Creator Initiative/Research Wiki 继续作为唯一主干。吸收 Story Bible、Style Guide、角色/场景/道具连续性、分镜级 Review/Retry、分阶段与断点续跑方法。
- 兼容性：Node 24.20.0、pnpm 9.15.0、FFmpeg/FFprobe 8.1.1 可用；Bun 缺失；ComfyUI 127.0.0.1:8188 本轮探测不可达。RX 6750 GRE 12GB 已有 Wan 2.1 1.3B 实测，不代表 MiniMax H3 上游 workflow 可用。
- 许可：上游主仓 MIT，但 skills/ 与 vendor/ 含独立许可内容；必须按文件审查，保留版权，不复制 vendor/openclaude 或第二 Agent/DB。
- 商业试点：10 条原创研究型短视频、30 天；先测受众留存与购买意向，再做模板/服务/合规联盟；平台分成仅记官方结算/到账。不得承诺收入或以播放量推算到账。
- IC-M1 第一切片已完成：`src/muse/story_bible.py` 提供版本化 Story Bible/Style Guide/Continuity schema 与引用校验；`muse-video plan --story-bible` 接入现有 storyboard DAG 预览，场景引用不匹配 fail-closed。Story Bible + video workflow 定向测试 21/21、全项目 unittest 1,176/1,176 通过。下一步 IC-M2：人物/场景/道具参考资产哈希注册与分镜级 review/retry，复用现有资产目录、attempt ledger 和 Creator Initiative，不复制上游 DB。

### B-137 粤语旁白 ASR、自动字幕与产品账本接通
- 已新增本地-only `cantonese_asr.py`，固定 Whisper 模型 revision，校验 `cts/model.bin` 大小与 SHA-256；CER 对稿、按 ASR 实际 segment 时间戳生成 SRT。
- `VideoProductBuilder` 可选执行 TTS→ASR→SRT→MP4/ZIP，manifest/storyboard 记录音频哈希、模型 revision、CER 与字幕哈希；`verify_path` 校验 ZIP 成员与来源关联。当前 Builder version 7（B-139 加入 BGM sidechain）。
- Python 3.14 TTS/ASR/VideoProductBuilder 定向回归 31/31 通过；包含结构级文件/ZIP/哈希验证及 mock ASR 编排测试。
- 当前边界：真实 TTS WAV 与视频封装此前已实机成功；Whisper 下载 Job 已 timeout，partial 文件不可用，真实 ASR/CER/SRT 与人工试听未验收。旁白默认启用与发布就绪仍关闭。
- 下一步：排查安全可靠的模型下载通道；真实评测 WAV ASR/CER/字幕同步；人工试听与真实 Wan 成片验收。音频响度/真峰值/峰值电平/RMS QA 已由 B-138 完成，旁白感知 BGM sidechain 已由 B-139 接通。
### B-138 视频音频质量检测
- `video_qa.py` 在完整解码时增加 EBU R128 + astats，报告 integrated LUFS、true peak、sample peak、RMS 与削波风险；指标缺失时明确标记 unavailable。
- sample peak >= -0.1 dBFS 仅作为削波风险启发式，不宣称精确 clipped-sample count；暂用 -24 至 -14 LUFS 与 -1 dBFS true-peak 作为审阅阈值，不自动改音量。
- 定向回归 42/42 通过；真实 FFmpeg 8.1.1 合成音 MP4 full decode 与音频指标读取通过。详见 `docs/PROGRESS_B138_VIDEO_AUDIO_QA.md`。- 下一步：精确削波样本统计评估、真实 Whisper 下载/ASR 与人工听感、真实 Wan 成片验收。

### B-139 旁白感知 BGM Ducking
- `package_generated_clip(..., background_music_path=...)` 接入 FFmpeg sidechaincompress：旁白作为 sidechain 控制背景音乐压低，人声与压低后的音乐混音。
- 原始 BGM 进入 ZIP，manifest/storyboard 记录 BGM 哈希、文件大小、archive path 与混音参数；`verify_path` 校验来源哈希。Builder version 7。
- 视频/粤语旁白定向回归 42/42；全项目回归 1,229 通过、1 跳过；真实 FFmpeg 8.1.1 sidechain 混音及 VideoProductBuilder 合成素材端到端 ZIP 验证通过。
- 合成音不代表真实粤语旁白或 Wan 成片验收；产品仍为 testing，ready_to_publish=false。详见 `docs/PROGRESS_B139_VOICE_AWARE_BGM_DUCKING.md`。

### B-164 MemPalace 选择性超融合
- 状态：MP-M1 适配器基础完成；MP-M2 D 盘固定版本安装、EmbeddingGemma 缓存和本机 stdio MCP smoke 完成（CPU 推理）；MP-M3 只读检索联邦完成但默认关闭；MP-M4 Agent Track outbox、Commander 审批 Ticket、checkpoint 回执校验、备份/恢复和合成 Palace 索引重建 smoke 已实现并验证；MP-M5 只读健康 API 与赛博控制台面板已接通，远端 MCP 在线探测、索引 freshness、pause/resume 与生产启用门禁仍未完成。
- 决策：MemPalace 只作为可选本地语义召回层；Muse MemoryStore 保留 User/Agent Track、审批与修订权威，Research Wiki 保留 claim/evidence 权威，TaskQueue/Commander 保留唯一调度权，Obsidian 继续单向镜像。
- 实装：mempalace_adapter.py 复用 Muse MCPStdioClient，固定 argv、离线模型缓存环境、严格 allowlist；MuseRuntime.search_memories 返回 unverified_retrieval_candidate，不混合评分。mempalace_projection.py 以 memory_id/revision + SHA-256 建立 outbox；发送要求绑定精确 outbox/idempotency key 的 Commander approval Ticket 与持久化 human.decision=approved 事件。回执必须确认恰好一条 added 或 exact-content duplicate 且 errors 为空，语义近似但内容不同的 duplicate 不算成功。
- 本机安装：固定 mempalace==3.12.0，D:\\AI AGENT\\MuseRuntime\\MemPalace-3.12.0 独立 venv；EmbeddingGemma 约 330 MB 缓存到 D 盘；PyPI wheel SHA-256 与 metadata/provenance subject 一致。Muse 原 .venv 未被修改。
- MCP 实测：本机 stdio 发现上游 47 工具，Muse 21 项 allowlist 全部存在；status/taxonomy/search 和真实 checkpoint 回执通过；相同合成内容重放返回 exact_duplicate 且 drawer ID 一致。未启动 MemPalace HTTP daemon 或 remote hub。
- 多语言 smoke：5 条合成粤语/中文/英文/时间/来源查询 Recall@5=5/5、Top-1=3/5；仅小型 smoke set，不代表正式 benchmark。生产 palace 仍为空且未初始化，未导入私人对话或 User Track。
- 安全边界：outbox 只允许 confidence>=0.75 的 Agent Track skill/case/foresight，凭据样式内容拒绝；Commander Ticket 绑定单条内容哈希和 revision，不能跨投影复用；User Track 仍禁止写入；不开放 mine/delete/sync-apply/hooks/远程管理。
- 回归：lifecycle 9/9、adapter 16/16、projection 12/12、ack contract 7/7，共 44 项 MemPalace 核心专项测试通过；UnifiedRuntime 46/46 另有专项覆盖。B189 Status API 3/3、UI Node tests 46/46、Svelte check 0/0、Vite build 134 modules 通过。最新全仓 Python 回归 1,558 tests OK、9 skipped；随后全仓 compileall 通过，详见 docs/PROGRESS_B189_MEMPALACE_DASHBOARD_STATUS.md。
- 最新全仓 Python 3.11 unittest：1,558 tests OK、9 skipped；并行写入期间 compileall 曾捕获瞬时语法状态，写入落盘后全仓 compileall 复跑通过。MemPalace 核心定向回归 90/90 的既有记录与 B189 UI/API 验证分开记录。
- 下一步 MP-M5：增加限频、显式启用的只读 MCP health probe 和可审计索引 freshness 信号；随后实现显式 pause/resume 状态机与可追溯错误报告。任何后台同步、服务自启动和生产 User Track 写入仍需独立审批。
- DirectML provider 尚未启用，当前明确 CPU 推理。

### B225 视频发布质量证据汇总
- 发布准备包新增 `release-quality-report.json`，统一记录技术 QA、视觉异常、字幕人工审批、事实/编辑、版权/模型权利、隐私/肖像、音乐权利、AI 披露、商业关系、平台规格与账号授权。
- 字幕批准必须与当前 MP4 / SRT 哈希绑定；任何版本变更使旧批准失效。其他人审缺少签署证据时一律保持 pending/blocked；技术 QA 通过不等于 release approval，`can_publish=false` 不变。
- 定向测试 6/6；Python 全量 1,746 tests，1 skipped，0 failures；compileall 通过。UI 51/51、Svelte 0 errors / 0 warnings、Vite 140 modules 通过。真实桌面窗口与平台发布仍未验收。

### B229 发布审核记录范围绑定
- 所有 release human review 必须绑定 reviewer ID/role、时区时间、产品 initiative/version、当前视频与目标产物哈希、证据引用、理由和记录摘要；摘要不是数字签名或身份认证。
- 字幕旧版 `approved` 文档不再直接解除发布阻塞；必须另有 `human_reviews.subtitle_review`，并绑定 SRT、字幕审核文档和当前 MP4 的精确哈希。证据引用的真实性/可解析性仍待接入证据注册表。
- 专项测试 7/7；Python 全量 1,749 tests，1 skipped，0 failures / 0 errors；compileall 成功。UI 51/51、Svelte 0 errors / 0 warnings、Vite build 140 modules。`can_publish=false`，没有发布 API 调用。

### B230 产物哈希格式校验
- 审核记录所有目标产物哈希必须为完整小写 SHA-256；缺失或非法值 fail closed，不能通过空字符串占位。
- `test_video_publish_package.py` 7/7 通过；B230 补丁后的 Python 3.11 全量回归 **1,749 tests，OK，1 skipped，0 failures / 0 errors**，compileall 成功。

### B234 结构化字幕审核接入发布审核链
- 审批 API/工作台要求审核者 ID、角色、审批理由、实际证据引用与明确口令；完整审核 cue 才能批准。
- 本地自报身份显式标记 `self_asserted_local_identity`，不冒充认证。新版本 Manifest 的 `human_reviews.subtitle_review` 绑定当前 MP4、SRT、审核文档哈希及产品版本，并由发布质量报告校验摘要和范围。
- 工作台增加结构化字段，证据引用默认空白；字幕审批只生成不可覆盖的新版本，不改变发布权限。
- 本地字幕审校 CLI 同步要求 reviewer ID/role、理由与重复 `--evidence-ref` 参数，避免绕过 API/工作台的结构化审核要求；CLI help 已验证。
- 证据注册表真实性验证和真实桌面视觉验收仍待完成。

### B251 FFmpeg Skill 安全 Cut 执行器
- 新增 probe-first 的本地 MP4 Cut：accurate 精确解码重编码、copy 快速关键帧对齐；输入/输出路径、时长、源文件指纹、HDR 决策和编码器能力均先检查。
- 固定 argv、`-nostdin`、`-n`，不接受 shell/filtergraph；输出在目标同卷临时目录生成，FFprobe + Muse full-decode QA 通过后才用原子 hard-link 发布，绝不覆盖已有目标。
- 自动优先本机 AMF，失败才回退 libx264；当前本机 FFmpeg 8.1.1 的真实 AMF Cut 与独立编码 smoke 均通过，fallback=False。
- Python 3.11 专项 20/20（FFmpeg Skill 6/6 + Cut 14/14）；CLI contract/doctor/dry-run、合成 MP4 probe、真实切片与完整解码 smoke 通过。42 项目录仍不代表 42 项功能已实现；下一步优先安全 join/fit。