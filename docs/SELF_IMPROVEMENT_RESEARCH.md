# Muse 自我改进与开源研究笔记

更新：2026-11-02

## 设计目标
Muse 的“进化”定义为可验证的工程改进循环，而不是无边界自我修改：发现能力缺口 → 形成假设 → 搜集来源 → 评估证据/许可证/安全 → 在隔离分支或沙箱提出改动 → 测试与独立审查 → 人工批准 → 发布 → 监测 → 必要时回滚。

## 参考项目与可吸收设计

### LangGraph：持久状态与人工中断
- 官方 Persistence 文档说明 checkpoint 可保存 thread 状态，支持中断后恢复；Store 用于跨 thread 的长期数据。
- 官方 Interrupts 文档说明执行可在需要外部决定时暂停，持久化状态后再恢复；工具调用可先让人审阅/修改。
- Muse 借鉴：任务状态与长期记忆分离；审批必须成为可恢复的持久状态，而不是只存在聊天记录。
- 不直接引入 LangGraph 依赖；先保持现有 SQLite 核心。
- 来源：https://langchain-ai.github.io/langgraphjs/how-tos/persistence-postgres/
- 来源：https://langchain-ai.github.io/langgraphjs/concepts/human_in_the_loop/

### OpenHands：Agent Server 与自动化调度分层
- OpenHands 仓库将 SDK/Agent Server、Canvas 前端、Automation Server 分成不同职责；自动化服务负责按计划或事件派发任务，Agent Server 决定具体执行。
- 项目介绍展示 signal → plan → execute → validate → ship → monitor 的工程闭环，并强调 sandbox/runtime 与人工控制。
- Muse 借鉴：调度器不应直接承载全部 Agent 逻辑；未来将调度、执行沙箱、任务工作区、监控拆开。
- 当前不复制 OpenHands 代码、不安装其运行时；先研究接口和边界。
- 来源：https://github.com/OpenHands/OpenHands
- 来源：https://www.openhands.dev/

### 递归自我改进研究：评估器必须独立
- 2026 年综述区分部署时行为改进、训练策略改进、评估器改进和研究流程改进；指出自我评估信号较弱，容易出现自我确认与评估器被钻空子。
- Muse 借鉴：每个改动要有可重复测试、基线比较及独立审稿；不能只让提出改动的 Agent 自己宣布成功。
- 来源：https://arxiv.org/abs/2607.07663

### Letta Code：Dreaming 與第二視角記憶審核
- Letta 官方文件說明 Dreaming 可由背景 subagent 檢視近期互動、整理長期有用經驗；觸發方式可按已完成步數或 context compaction，亦可選擇第二個 Agent 審閱記憶提案。
- Muse 可吸收成「Dream Cycle」：每累積 N 個已驗證工作步驟，或每個 30 分鐘窗口結束後，從已脫敏的結構化事件整理經驗；另一個獨立角色檢查重複/錯誤/隱私，最後形成提案，而非直接改寫核心規則。
- 創新融合方向：讓 Dream Cycle 同時產生能力缺口、研究問題、可重用技能草案及下一窗口任務，並以證據連結每項建議；這比單純反思更接近持續研發閉環。
- Muse 暫不複製 MemFS 或依賴 Letta runtime；先用現有 SQLite + Markdown 實現可審計版本。
- 來源：https://docs.letta.com/configuration/memory
- 來源：https://github.com/letta-ai/letta-code

### OpenHands Agent Server + Automation：觸發層與執行層分離
- OpenHands 將 Agent Server 作為多 Agent 執行 API，Automation Server 負責排程/事件觸發與派發；Canvas 只負責呈現和控制，不直接執行 Agent 動作。
- Muse 可吸收為三層：Scheduler 只派發帶有時間預算/資源限制的任務；Worker/Agent Runtime 負責執行；Report UI/CLI 負責展示事件、暫停與審批。如此可避免一個巨型 Worker 同時負責排程、推理、執行與通知。
- 創新融合方向：用半小時工作窗口作為「微型研發 Sprint」，窗口內並行研究、實作、驗證，窗口末由 Reporter 匯總；未完成任務保留 checkpoint，下個窗口接續。
- 不複製 OpenHands 程式碼、不引入其依賴；先採用服務邊界及事件協議。
- 來源：https://github.com/OpenHands/OpenHands/blob/main/docs/architecture.md
- 來源：https://github.com/OpenHands/OpenHands
- 來源：https://github.com/OpenHands/software-agent-sdk/tree/main/openhands-agent-server

### LangGraph / OpenHands SDK：可中斷的事件驅動 Agent 步驟
- OpenHands SDK 的 Agent 架構以事件歷史作輸入/輸出，單一步驟可中斷及恢復；LangGraph 的 durability modes 則讓執行速度與 checkpoint 持久性作明確取捨。
- Muse 可將每個 Agent step 視為原子工作：先記錄意圖/輸入摘要，再執行工具，記錄觀察結果，最後保存 checkpoint；審批中斷、程序崩潰或時間窗口結束時都可恢復。
- 創新融合方向：將任務 checkpoint 與「時間預算」結合，Agent 每一步先估算剩餘窗口；接近窗口末端時只完成安全收尾及 checkpoint，不啟動無法在窗口內妥善收尾的新工作。
- 來源：https://github.com/OpenHands/docs/blob/main/sdk/arch/agent.mdx
- 來源：https://docs.langchain.com/oss/python/langgraph/durable-execution

## 形成中的 Muse 方案
1. Capability backlog：持续记录能力缺口、影响、证据、优先级和下一步。
2. Research ledger：记录搜索问题、来源 URL、日期、结论、可信度和许可证/安全审查。
3. Proposal gate：提案经过 proposed → researching → ready_for_review → approved → implemented；可 rejected / rolled_back。
4. Sandbox-first：新开源代码先核验许可证、依赖、维护状态、已知漏洞，再在隔离工作区评估；未知许可证不复制代码。
5. Independent verification：测试由独立验证任务执行；提出者不能单独批准自己的改动。
6. Progress events：每个状态变化和验证结果写入持久事件流，供 UI/CLI 实时展示。
7. Budget governor：只用已核实免费额度；本地并发按 CPU/RAM/GPU 资源预算调整。

## 重要边界
- “研究项目”不等于自动 clone、安装或执行；默认先读公开文档/代码页面。
- 未知/不兼容许可证不复制源代码；优先借鉴概念并自行实现。
- 第三方代码必须静态检查、依赖审查、隔离测试；不允许未经审查直接进入主分支。
- 自我改进只能提出候选变更；正式启用前需要明确审批、回滚点和验证证据。
- 不把模型自评当作唯一正确性证据；高风险行为需人工审核。
- 不允许为制造 24 小时活动而空转；无任务时应等待新事件或下一次计划触发。

## 后续研究问题
- 如何从失败日志与重复人工修正中提取能力缺口，同时过滤隐私内容？
- 如何定义多 Agent 的独立验证者，避免同一模型家族的共同盲点？
- 如何将任务级测试结果转成跨版本基准，防止改进导致能力倒退？
- 如何实现 Windows 开机启动、进程监督、心跳与任务租约恢复，同时允许用户一键暂停？
