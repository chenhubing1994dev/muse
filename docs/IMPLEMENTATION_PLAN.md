# Muse 并行实施计划

## 工作流 A：本机环境与工具
- CPU：AMD Ryzen 5 7500F，6 核 12 线程；内存约 39.6 GB；GPU：AMD Radeon RX 6750 GRE，12 GB 显存。
- 已确认：Python Launcher 3.14.5、Node.js 24.20.0、npm 11.19.0、Git 2.56.0、VS Code 1.128.0 可调用。
- 已安裝 LM Studio 0.4.26；最新 `lms ls` 確認三個本地模型：`eva-abliterated-ties-qwen2.5-14b-i1`（14B，6.66 GB）、`voxcpm2`（1.33 GB）、`text-embedding-nomic-embed-text-v1.5`（84.11 MB），合計約 8.08 GB。`lms ps` 確認目前沒有模型載入；尚未進行本地推理測試。- python.exe 是 Microsoft Store 执行别名；应使用 py -3.14。另发现 Python 3.11、3.12、3.13、3.14 多个安装。
- 当前 PATH 中未找到 Ollama 与 Docker 命令；这不代表电脑绝对没有相关软件。
- D 盘已有 D:\Models 约 69.13 GB；发现 Gemini、Grok、Hermes、DeepSeek、OpenCode、Codex 等工作目录。目录存在不等于程序已正确安装或可运行，仍需逐项验证。
- 原则：下载、安装、模型和项目数据统一优先放 D 盘；不擅自安装、下载或移动/删除文件。
- 用户补充的 API 调度规则：用户会提供 DeepSeek Harness（名称待按实际安装目录确认）可用的免费模型 API；Muse 可在确有任务需要时调用它辅助研究/执行。调用前检查当日免费额度或剩余额度；额度耗尽立即停止调用，不重试、不产生付费请求，等待额度重置。把额度检查、调用次数、任务用途和结果写入审计记录。
- 硬件调度原则：CPU/GPU 不以长期 100% 占用为目标。交互期间优先保证语音/前台响应；后台研究、索引和本地推理按资源预算并发，监测温度、显存、内存与负载，空闲时提高利用率，用户交互时自动降载。不得为追求利用率而启动未经确认的服务或安装软件。

## 工作流 B：架构与开源研究
- 采用自有轻量核心，先不绑定单一 Agent 框架。
- 任务状态：queued、running、waiting_approval、succeeded、failed、cancelled。
- 每个任务保存目标、步骤、来源、工具摘要、产物、验证结果、错误和 next_action；用幂等键避免重复执行，用审计事件记录每个外部动作。
- 记忆与任务分离；记忆需记录来源、时间、置信度，并允许用户查看、导出、修订。
- 多条工作线由调度器并发管理；每条工作线有独立任务队列和资源限制，遇到高风险动作进入 waiting_approval。
- 研究、执行、测试、汇报分工；汇报后由调度器按队列继续，而不是依赖当前聊天永不结束。
- Letta 参考跨会话持久身份、MemFS 记忆和后台 dreaming；OpenJarvis 参考本地优先、模型引擎抽象、工具/记忆和定时 Agent；LangGraph 参考 checkpoint、跨会话 store、暂停审批和故障恢复。
- 参考资料：
  - Letta Stateful Agents: https://docs.letta.com/concepts/stateful-agents
  - Letta Memory & Dreaming: https://github.com/letta-ai/letta-docs-md/blob/main/configuration/memory/index.md
  - OpenJarvis: https://open-jarvis.github.io/OpenJarvis/
  - OpenJarvis Architecture: https://open-jarvis.github.io/OpenJarvis/architecture/overview/
  - LangGraph Persistence: https://docs.langchain.com/oss/python/langgraph/persistence

## 本轮验证
- py -3.14 -m unittest discover -s tests -v：1 项测试通过，退出码 0。
- PYTHONPATH=src; py -3.14 -m muse.main --check：退出码 0；因本地 config/muse.toml 尚不存在，CLI 正常提示复制样例配置。没有启动后台服务。
- 硬件与模型盘点均为只读检查；未下载、安装、移动或删除任何内容。

## 本轮新增进展（2026-11-02）
- WebCodex Runner 已重新核验在线：Runner 1/1 online，WebSocket connected，协议兼容、构建版本一致；当前 active/running/queued/recovering Job 均为 0。
- 发现项目仍是轻量骨架：原先只有配置加载与一个配置测试，没有任务队列实现；因此尚无独立 worker/后台守护进程。
- 按 TDD 新增 `tests/test_task_queue.py`，先观察到因 `muse.task_queue` 不存在而失败，再新增 `src/muse/task_queue.py`。
- 新队列使用 SQLite + WAL 持久化任务，支持 enqueue、读取、按 FIFO 原子领取 queued 任务并转为 running；任务数据库关闭后重新打开仍可恢复。
- 全量测试：`py -3.14 -m unittest discover -s tests -v`，3 项通过，退出码 0。
- 当前只实现了持久队列基础层；尚未实现常驻 worker、失败重试/崩溃恢复策略、资源监控、通知通道或多 Agent 执行器。没有启动服务，也没有声称网页关闭后任务会继续运行。

## 记忆原型进展（2026-11-02）
- 新增 `src/muse/memory.py`：SQLite + WAL 的本地记忆库，支持新增、读取、AND 关键词检索、项目筛选、修订、软删除和 JSON 导出。
- 新增 `memory_revisions` 历史表；修订时保存上一版本内容、来源、置信度和时间，支持 `history(memory_id)` 查询。
- 新增 `src/muse/memory_cli.py`，可从命令列新增、搜索、查看历史和导出记忆；默认数据库路径为 `data/memory/muse.sqlite3`，仅在实际运行 CLI 时才会创建。
- 新增持久化重开测试和修订历史测试。最新全量测试实跑：10 项通过，退出码 0。
- 早期盤點曾只看到 VoxCPM2 與 Nomic Embedding；2026-11-02 再次實際執行 `lms ls` 後，確認另有 Qwen 2.5 14B 模型 `eva-abliterated-ties-qwen2.5-14b-i1`。`lms ps` 顯示目前沒有載入模型；尚未啟動推理或下載新權重。- 开源研究：Letta 的 Agent 记忆跨 conversations 共用；LangGraph 区分 thread-scoped checkpoints 与跨 thread stores；OpenJarvis 采用可替换 InferenceEngine 和独立 Memory 层。Muse 先吸收接口与数据边界，不引入大型框架依赖。
- 本阶段没有启动服务、下载模型、安装依赖或创建正式个人记忆数据。
## 对话与 Worker 原型进展（2026-11-02）
- 新增 `src/muse/conversations.py`：按 thread_id 保存独立会话消息 checkpoint，支持更新版本号、列出线程和数据库重开恢复。
- 新增 `src/muse/model_client.py`：标准库实现 LM Studio OpenAI-compatible `/models` 与 `/chat/completions` 客户端；连接失败转换为清晰的 ModelError。
- 新增 `src/muse/assistant.py`：模型调用成功后才写入会话，模型失败不会覆盖已有 checkpoint。
- 新增 `src/muse/chat_cli.py` 与 `muse-chat` 入口：支持指定 thread、SQLite 数据库、LM Studio base URL 和可选模型 ID；每次调用完成后打印回复及 checkpoint revision。
- 扩展 `TaskQueue.finish()`：支持 succeeded/failed/cancelled/waiting_approval 状态，并将 handler 结果写回任务 payload。
- 新增 `src/muse/worker.py`：one-shot Worker 只调用显式注册的任务处理器；默认只有无外部副作用的 echo 示例，未知 kind 失败关闭。未启动常驻循环或系统服务。
- 新增 checkpoint、模型客户端、assistant 与聊天 CLI 测试；本轮另以本机 HTTP 测试服务器验证真实 HTTP 请求/响应。最新全量测试实跑：20 项通过，退出码 0。
- LM Studio 客户端已验证协议与持久对话流程，但本机尚未对真实聊天模型完成推理验证；未启动 LM Studio、下载模型、安装第三方依赖、创建正式个人记忆数据或配置开机自启。
## 可运行聊天雏形进展（2026-11-02）
- 新增 `src/muse/assistant.py`：将本地模型调用与 ConversationStore 串接；每个 thread 独立保留 system/user/assistant 消息，模型失败时不会覆盖既有 checkpoint。
- 新增 `src/muse/chat_cli.py` 与 `muse-chat` 入口：支持指定 thread、SQLite 数据库、LM Studio base URL 和可选模型 ID；每次调用完成后打印回复及 checkpoint revision。
- 新增 assistant 与 CLI 持久化测试；当前全量测试实跑：19 项通过，退出码 0。
- `pyproject.toml` 注册 `muse-chat` 与 `muse-memory` 两个命令入口；待验证 TOML 解析及 CLI help。
- 真实端到端模型响应仍待条件：当前 LM Studio server 未运行，已发现的本地模型目录尚无已确认可聊天的 LLM。没有擅自启动服务、下载权重或安装依赖。
- 开源设计对照：Letta 提供跨对话共享记忆及后台整理思路；LangGraph 将线程 checkpoint 与跨线程 store 分开；OpenJarvis 将模型推理 Engine、Agent、Memory 与工具层解耦。Muse 保持轻量自有实现，暂不引入整套框架。

## 本机模型接入检查（2026-11-02）
- `lms ps` 顯示目前沒有已載入模型；最新模型庫清單包括 Qwen 2.5 14B、VoxCPM2 語音模型與 Nomic Embedding 模型。LM Studio CLI 服務喚醒不等於聊天推理伺服器已啟動。
- 過往 `muse-chat` 端到端探測曾因 LM Studio endpoint 未就緒返回 HTTP 502；目前仍未完成 Qwen 2.5 14B 的真實推理驗證。
- 目前已找到可用的聊天模型候選，不需立即下載 Qwen3；下一步先確認 LM Studio Models Directory 位置、模型載入所需 RAM/VRAM 與量化版本，再由使用者批准後做一次限資源推理測試。
- 不啟動模型載入、不啟動 Local Server、不下載模型、不安裝依賴，直到資源與路徑核驗及必要批准完成。 
## 自主执行安全门槛（2026-11-02）
- `TaskQueue.approve(task_id, approved_by=...)` 只接受 `waiting_approval` 任務，以 SQLite `BEGIN IMMEDIATE` 原子寫入批准人/時間、標記 `_approved` 並重新排入隊列；重複/越權批准會失敗關閉。
- `worker.run_once(..., approval_required_kinds={...})` 對指定 kind 強制檢查 `_approved is True`，未批准只會寫入 `waiting_approval` 狀態，不調用 handler。
- 新增 `muse-task approve TASK_ID --by user` 命令入口；不新增常駐背景進程。
- TDD 先新增測試並確認失敗，再實現批准狀態轉換與 Worker 門檻；最新全量測試實跑：24 項通過，退出碼 0；`pyproject.toml` 已通過 Python TOML parser。

## 本機資源盤點（只讀，2026-11-02）
- CPU：AMD Ryzen 5 7500F，6 核 12 線程；GPU：AMD Radeon RX 6750 GRE 12GB；RAM 約 39.6GB（與先前盤點一致）。
- 磁碟剩餘：C 約 52.1GB、D 約 406.9GB、E 約 24GB。D 盤有充足空間，但 LM Studio 實際 Models Directory 尚未由本次只讀檢查確認，因此仍未下載模型。
- LM Studio Server 未運行、目前沒有載入模型；磁碟中已知只有 VoxCPM2 語音模型與 Nomic Embedding 模型。未啟動服務、載入模型、跑 GPU 壓力測試或安裝任何套件。

## 新增架構研究方向（2026-11-02）
- Letta：借鑑持久 Agent、跨會話記憶與 sleep-time consolidation；Muse 應以低頻、可暫停的整理任務實現，而非宣稱無限背景思考。
- Agent Zero：借鑑多模型分工、檢索知識庫與模型生命週期管理；先設計模型角色/資源策略，不直接引入其整套依賴。
- Open-Sable：借鑑目標管理、反思與受控工具調用；先把目標、審批、審計和恢復做穩。
- Agent OS：借鑑 plan → execute → verify → recover → approve 的閉環；每項任務必須有可驗證結果與 no-progress 停止條件。
- 參考：Letta https://www.letta.com/agent/；Agent Zero https://github.com/thefilesareinthecomputer/agent-zero；Open-Sable https://github.com/IdeoaLabs/Open-Sable；Agent OS https://github.com/earthwalker17/agent-os。

## 多 Agent 核心实施进度（2026-11-02）
- 新增 `src/muse/orchestration.py`：以 AgentSpec 注入不同角色 callable，使用标准库 ThreadPoolExecutor 并行执行；并发上限可设为 1–10，默认 5。
- 每个 Agent 的 queued/running/final 状态及输出、错误写入独立 SQLite 审计表；父运行状态区分 succeeded/partial/failed。
- 单个 Agent 失败不会抹掉其他 Agent 成功结果；新增离线测试覆盖并行执行、运行结果持久化、失败隔离及并发上限。
- 全量测试实跑：32 项通过，退出码 0；包含双 Agent Barrier 并发测试与免费额度 fail-closed 测试。
- 静态验证：pyproject.toml 成功解析；src/muse 全部模块 compileall 通过。

## 外部平台研究进度（2026-11-02）- 新增 `docs/PROVIDER_RESEARCH.md`，核对 WorkBuddy MCP、Coze API、Gemini API 免费层/配额，以及 OpenAI Agents SDK 的编排、审计和人工审核设计。
- 初步判断：WorkBuddy 更适合作为 MCP 工具/执行工作台研究；Coze 免费 API 文档列出 30 次调用额度，不能视为无限免费；Gemini Apps AI Pro 与 Gemini Developer API 配额/计费分开核验。
- 所有云端 provider 默认关闭；额度未知或耗尽即停止，不付费 fallback。未发送个人文件、记忆或真实用户资料至云端。

## 免费额度保护进度（2026-11-02）
- 新增 `src/muse/provider_policy.py`：FreeOnlyRouter 仅选择免费层已确认、额度已核验且余额大于零的 Provider；未知、耗尽、付费-only 全部 fail-closed。
- 新增额度消耗边界测试，验证不会超扣或自动付费 fallback。Provider 的真实额度查询仍需逐个平台 adapter 实现。

## 跨会话自主工作规程
- 新增 `docs/AUTONOMOUS_WORK_CYCLE.md`：规定新会话必须先读 `docs/PROGRESS.md`，再读架构、计划和能力 Backlog；核对当前状态后按优先级恢复工作。
- 每项工作遵循 plan → TDD → implement → review → validate → record → update docs；明确写出 next action，避免每次对话重新规划。
- 规程明确 ChatGPT 不会在会话结束后后台运行；Windows 服务/开机自启仍须用户单独批准。

## 下一阶段自主工作顺序
1. 进度事件第一阶段已完成；下一步把 Worker 任务生命周期（queued/running/waiting_approval/completed/failed/recovered）自动接入 ProgressEventStore。
2. 以结构化测试失败类型、任务状态聚合与重复修正标签，自动建立可排序 capability proposals；不读取私人对话或任务 payload。
3. 建立独立审稿 Agent 与可重复基准；提案者不得批准自己的改动。
4. 研究并验证 WorkBuddy MCP 与 Muse 工具注册表的安全桥接方式，默认只读、项目级授权。
5. 逐一验证 ProviderAdapter 的错误/超时/额度保留边界；未核实官方免费层与即时余额前不启用云端请求。
6. 最后评估 Windows 服务/开机自启、资源预算、通知与停止控制；部署前需单独确认。

## 本機自檢 Watchdog 第一階段（2026-11-02）
- 新增 `src/muse/watchdog.py` / `muse-watchdog --once`，讀取既有 SQLite 任務隊列與進度事件，檢查狀態數量、過期租約及最近事件時間。
- 報告不包含任務標題、payload 或租約持有人；資料庫不存在時不建立新資料庫。
- 每次檢查將固定摘要寫入 ProgressEventStore，供 `muse-progress` 查詢。
- TDD 新增 3 項測試；全量 unittest 60 項通過，TOML、compileall、CLI help 驗證通過。
- 對本機正式任務資料庫實跑一次：healthy / idle，queued=0、running=0、waiting_approval=0、failed=0、expired_leases=0。
- 已按使用者要求註冊 Windows Task Scheduler 每小時觸發 `scripts/muse-watchdog.cmd`；目前為 Interactive only，僅在 Administrator 登入期間執行。下一步：加入 Worker/Runner 進程與工具連線探測，再完善可暫停/告警的自檢流程。

## 每小時工作量估算與容量規劃第一階段（2026-11-02）
- 新增 `src/muse/workload.py` / `muse-plan-hour`：每小時 3600 秒、預留 300 秒自檢，按 priority 與 estimated_seconds 進行 first-fit 排程；較大任務延後後仍會繼續嘗試較短工作。
- `TaskQueue` 儲存 priority/estimated_seconds，舊資料庫自動遷移；Worker 按 priority 高至低領取。
- Watchdog 每小時一併輸出 workload capacity 狀態，事件摘要包含 queued 數與容量狀態。
- 全量 unittest 67 項通過；TOML、compileall、兩個 CLI 實際執行通過。
- 本機目前 queued=0，容量狀態 underfilled；所以仍要完成安全的 Backlog-to-task 產生器及對應 handler，先有真實工作，先可以合理填滿時段。
- 下一步：以可執行的本機工作類型建立工作產生器；加入實際耗時紀錄及估算校準；再做每小時有界執行窗口。未有合適任務時應研究/整理新提案，不能空轉。

## 常駐執行引擎進度（2026-11-02）
- 已實現 run_forever() 持續消費任務；空隊列使用可中斷輪詢等待，支援 stop event。

- SQLite 任務租約/心跳/過期回收已實作；舊資料庫自動遷移，舊 Worker 無法完成已被接管的任務。
- 崩潰回收最多嘗試 3 次；一般 handler 失敗不自動重試，避免把業務錯誤無限重跑。
- 最新全量 unittest：52 項通過；compileall、pyproject.toml TOML 解析及 Worker CLI help 通過。
- 尚未部署 Windows 常駐服務/開機自啟；對話或工具會話結束後，不會由 ChatGPT 自行在背景無限運作。

## 自我改进与开源研究进度（2026-11-02）
- 新增 src/muse/improvement.py，记录提案、研究来源、许可证/安全审查与状态事件；只有来源审查合格并经明确批准，才可进入 implemented。
- 新增 src/muse/improvement_cli.py 与 muse-improve 入口，可新增/列出/查看提案、登记来源、推进状态和审批。
- 新增 docs/SELF_IMPROVEMENT_RESEARCH.md，整理 LangGraph 持久 checkpoint/HITL、OpenHands 自动化与 Agent Server 分层、递归自我改进中独立评估器的设计启发。
- 6 项新增测试通过；全量 unittest 40 项通过。TOML、compileall 与 CLI help 验证通过。研究只读取公开网页/文档，没有 clone、安装或执行第三方仓库。
- 下一步：把任務失敗/重複修正轉成 capability backlog；加入獨立審稿與可重複基準；擴充 progress events；完成 ProviderAdapter 及安全 MCP 橋接研究；再評估 Windows 常駐服務。