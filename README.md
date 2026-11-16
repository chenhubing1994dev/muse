# Muse — 个人 AI 伙伴

Muse 是一个面向个人长期协作的 AI 伙伴项目。目标是把对话陪伴、个人知识与记忆、主动任务编排，以及经授权的电脑/项目执行整合成一个可审计、可逐步扩展的系统。

## 当前阶段

目前已實現可測試的本地 Agent 雛形：SQLite 長期記憶與修訂歷史、對話 checkpoint、持久任務隊列與人工批准/恢復流程、帶租約/心跳的 Worker、進度事件及 Watchdog、自適應閒置監測、多 Agent 並行編排、改進提案審核、LM Studio 模型客戶端、自主規劃器、唯讀研究介面、持久化 Routine Scheduler，以及事件准入、結構化規則評估、耐久 Outbox 和人工批准工作提案。來源入口包括離線 fixture，以及需明確指定公開倉庫的唯讀 GitHub Issues adapter；尚未連接飛書/IM、webhook、私有倉庫或背景持續輪詢。未部署 Windows 常駐服務，亦未宣稱 ChatGPT 會在對話結束後於雲端持續運作。
- `config/`：非敏感配置样例
- `data/memory/`：本地记忆数据目录（运行数据不纳入版本控制）
- `data/tasks/`：任务状态数据目录（运行数据不纳入版本控制）
- `logs/`：本地日志目录
- `tests/`：基础测试
- `docs/`：架构与安全边界
- `docs/PROGRESS.md`：持续进度、验证结果与下一步
- `docs/VISION.md`：Muse 四大核心支柱与长期产品宪章
- `docs/UPSTREAM_FUSION_RESEARCH.md`：Super Agent Party 与 dsh-superagent 源码审查及 Muse 融合路线

- `docs/UI_PRODUCT_SPEC.md`：Cyber-Mythic Pixel 桌面调度中心产品规格
- `docs/API_CONTRACT.md`：本机只读状态 API v1 契约
- `ui-prototype/`：真实 API 驱动的只读桌面 UI（无连接时明确显示空态/错误）
- `desktop/src-tauri/`：Tauri 2 + Rust 桌面壳源码；Release EXE 位于 `desktop/src-tauri/target/release/muse-desktop.exe`，启用 `custom-protocol` 后使用内嵌生产前端，不依赖 Vite 1420 端口。
- `src/muse/local_dashboard.py`：loopback-only 本地浏览器控制台宿主，向同源 UI 临时注入内存 API 会话，不落盘 token。
- `scripts/Muse-Desktop.ps1`：构建生产 UI 与 Tauri Rust 客户端并启动桌面版；`scripts/Install-Muse-Desktop-Shortcut.ps1` 创建 `Muse.lnk` / `Muse-Desktop.lnk` 桌面快捷方式。
- `scripts/Muse-Start.ps1` / `scripts/Muse-Stop.ps1`：日常启动/停止本地自主运行时和浏览器控制台。
## 环境要求

- Python 3.11 或以上
- 仅使用 Python 标准库；当前无需安装第三方依赖

## 快速检查

在项目根目录执行：

```powershell
$env:PYTHONPATH = 'src'
py -3.14 -m unittest discover -s tests -v
py -3.14 -m muse.main --check
py -3.14 -m muse.memory_cli --help

# 當 Muse 本機進度靜默時，自動產生續接提示（預設單次檢查）
py -3.11 -m muse.idle_watch --once
# 持續本機監測；Ctrl+C 停止，不會自動傳訊息入 ChatGPT
py -3.11 -m muse.idle_watch --loop --interval 60
# 由本機 LM Studio 模型每 30 分鐘提出一項 Muse 下一步（需先啟動 LM Studio）
py -3.11 -m muse.autonomy --loop --interval-seconds 1800
# 查看已保存的自主規劃提案
py -3.11 -m muse.autonomy --list
# 批准/拒絕提案（PLAN_ID 使用清單中顯示的實際 ID）
py -3.11 -m muse.autonomy --approve PLAN_ID --reviewed-by local-user
py -3.11 -m muse.autonomy --reject PLAN_ID --reviewed-by local-user --reason "not now"
# 查看已批准任務並執行一次 Worker（會建立 data/approved_plans/ 工作包）
py -3.11 -m muse.worker --db data/tasks/muse.sqlite3
# 可选：按实时 CPU/RAM 快照自动选择安全并发；32 是硬上限，默认 Worker 仍为单并发
py -3.11 -m muse.worker --auto-workers --max-workers 32
# 基于 User Track 历史记忆生成象征视角灵感简报（离线、只读、不自动写回）
py -3.11 -m muse.inspiration "为 Muse 设计下一阶段的突破方向" --project muse
# 使用已配置的 SearXNG 搜尋公開資料並保存來源（先設定 MUSE_SEARXNG_URL）
py -3.11 -m muse.research "Muse autonomous agent frameworks"
# 多角度并行研究（Bing RSS 无需 API key；证据与脱敏进度事件分别保存到本地 SQLite）
py -3.11 -m muse.research_pipeline "Muse Ruflo official documentation" --angle "LM Studio local server API" --provider bing-rss
# 通过统一测试入口运行 unittest，并把开始/结果写入本机进度账本
py -3.11 -m muse.test_runner --start-directory tests
# 对单一已排队 Ticket 执行 Muse Commander DAG（需要显式确认及已配置的 Ruflo Provider：Anthropic 或 ChatAnywhere）
# 默认只尝试本机 LM Studio（MUSE_LMSTUDIO_URL/MUSE_LMSTUDIO_MODEL）；不可用时自动回退到已验证的安全 DAG 模板
py -3.11 -m muse.unified_runtime commander-once --task-id TICKET_ID --approve --max-workers 4
# 一次性 ChatAnywhere -> Ruflo -> Muse 真实 Agent 验收：隐藏输入 Key、使用临时工作区、测试后停止 Ruflo daemon，不保存 Key
py -3.11 -m muse.chatanywhere_smoke
# 建立每 30 分鐘自主規劃 routine，並在前台啟動排程器（Ctrl+C 停止）
py -3.11 -m muse.routines add autonomous-planner autonomy_plan --interval-seconds 1800
py -3.11 -m muse.routines --list
py -3.11 -m muse.routines --loop
# 建立本機提醒（時間必須包含時區），查詢到期提醒並確認完成
py -3.11 -m muse.reminders add --title "Review Muse" --message "Check the next milestone" --at "2026-11-02T18:00:00+08:00"
py -3.11 -m muse.reminders due
py -3.11 -m muse.reminders ack REMINDER_ID
# 預覽已到期提醒（不顯示、不標記已通知）；正式執行會呼叫 Windows 通知區氣泡
py -3.11 -m muse.reminders notify-due --dry-run
py -3.11 -m muse.reminders notify-due
# 自然语言建立提醒（支持「提醒我半小时后检查报告」「明天 09:30 提醒我开会」）
py -3.11 -m muse.reminders capture --text "提醒我半小时后检查报告" --timezone Asia/Singapore
# 延后提醒；通知默认 22:00–08:00 静音，可显式关闭静音窗口
py -3.11 -m muse.reminders snooze REMINDER_ID --until "2026-11-02T20:00:00+08:00"
py -3.11 -m muse.reminders notify-due --no-quiet-hours
# 从自然语言意图入口明确保存提醒（不带 --create-reminder 只预览计划）
py -3.11 -m muse.intent_cli "提醒我半小时后检查报告" --create-reminder --timezone Asia/Singapore

# 本機事件准入試跑（示例策略預設 observe，不會自動派工；先替換已核實的 actor ID）
py -3.11 -m muse.intake_runtime --policy config/intake-policy.example.json --fixture config/intake-fixture.example.json --intake-db data/intake/muse.sqlite3 --task-db data/tasks/muse.sqlite3
# 只讀拉取一個公開 GitHub 倉庫的 open issues（需明確指定 repo；不使用 Token；最多一頁 100 條）
py -3.11 -m muse.intake_runtime --policy config/intake-policy.example.json --github-repo OWNER/REPO --intake-db data/intake/muse.sqlite3 --task-db data/tasks/muse.sqlite3

# 只有你明確把 policy 的 mode 改成 auto，且規則匹配，才會建立工作提案；Worker 仍會先暫停等待人工批准
# 查看任務、批准 TASK_ID，批准後 Worker 只會產生本地 proposal packet，不會執行來源 payload
py -3.11 -m muse.worker --db data/tasks/muse.sqlite3
py -3.11 -m muse.task_cli --db data/tasks/muse.sqlite3 approve TASK_ID --by local-user
py -3.11 -m muse.worker --db data/tasks/muse.sqlite3

# 已存在的 \\MuseHourlyWatchdog 每 30 分鐘呼叫 watchdog 腳本，會順帶檢查並通知到期提醒


# 与本地 LM Studio 模型聊天（需先在 LM Studio 手动启动服务并加载聊天模型）
py -3.14 -m muse.chat_cli "你好，Muse" --thread main
# 后续继续同一段对话，历史会自动从 SQLite 恢复
py -3.14 -m muse.chat_cli "继续刚才的话题" --thread main
# 若 LM Studio 已加载聊天模型并启动 Local Server，可在同一 thread 继续聊天；默认地址 http://127.0.0.1:1234/v1。
# Chat CLI 会在本地记忆库已存在时检索相关的手动保存记忆；临时上下文不会写回聊天历史
py -3.14 -m muse.chat_cli "请用粤语回复" --thread main --memory-db data/memory/muse.sqlite3
# 单轮禁用记忆读取
py -3.14 -m muse.chat_cli "这次不要读取旧记忆" --thread main --no-memory

# 保存与检索记忆（首次执行会创建本地 SQLite 数据库）
py -3.14 -m muse.memory_cli add "这里填写要保存的内容" --project general --source manual
py -3.14 -m muse.memory_cli search "关键词"
py -3.14 -m muse.memory_cli export
py -3.14 -m muse.memory_cli list --project general

# 记忆分类与双轨：用户记忆与 Agent 操作经验分开保存/检索
py -3.14 -m muse.memory_cli add "偏好简洁粤语回复" --project profile --source confirmed --kind preference --track user
py -3.14 -m muse.memory_cli propose "Muse 发布前必须执行全量测试" --project Muse --source runbook --kind skill --track agent
py -3.14 -m muse.memory_cli search "发布测试" --track agent --kind skill
py -3.14 -m muse.memory_cli list --track user --kind preference
py -3.14 -m muse.memory_cli revise MEMORY_ID "修订后的内容" --confidence 0.9
py -3.14 -m muse.memory_cli history MEMORY_ID
py -3.14 -m muse.memory_cli delete MEMORY_ID  # 软删除，保留历史
 
# 从最近 24 小时的脱敏运行指标生成排序改进提案（不会自动批准或执行）
py -3.11 -m muse.capability_radar --task-db data/tasks/muse.sqlite3 --improvement-db data/improvements/muse.sqlite3
# 按优先级查看提案；继续研究、补来源和审批仍需人工操作
py -3.11 -m muse.improvement_cli --db data/improvements/muse.sqlite3 list
 
# 显式记录结构化反馈（只存分类标签与严重度，不保存自由文本）
py -3.11 -m muse.feedback_cli --db data/improvements/muse.sqlite3 add --category reliability --tag repeated_manual_fix --severity 4
py -3.11 -m muse.feedback_cli --db data/improvements/muse.sqlite3 list --since-hours 24

# 本机桌面控制台 API（Tauri 会自动启动；CLI 手动启动时需提供各数据库路径）
py -3.11 -m muse.status_api --task-db data/tasks/muse.sqlite3 --progress-db data/progress/muse.sqlite3 --capability-db data/capabilities/muse.sqlite3 --agent-db data/agents/muse.sqlite3 --improvement-db data/improvements/muse.sqlite3 --port 0
# 桌面控制台可记录结构化回馈、查看提案，并由用户手动触发 24 小时能力雷达扫描。
# Obsidian Vault（只讀稽核，不回寫）
py -3.11 -m muse.memory_cli export-obsidian --vault 'D:\\AI AGENT\\Muse\\vault'
py -3.11 -m muse.memory_cli audit-obsidian --vault 'D:\\AI AGENT\\Muse\\vault'

# 放入一項安全的 echo 示例任務，再由 one-shot Worker 執行
py -3.14 -c "from muse.task_queue import TaskQueue; q=TaskQueue('data/tasks/muse.sqlite3'); print(q.enqueue('echo demo', {'kind':'echo','input':'hello Muse'})); q.close()"
py -3.14 -m muse.worker --db data/tasks/muse.sqlite3

# 批准一個已進入 waiting_approval 的任務（TASK_ID 換成實際任務 ID）
py -3.14 -m muse.task_cli --db data/tasks/muse.sqlite3 approve TASK_ID --by user
# 取消尚未開始或正在等待批准的任務（不會強制終止 running handler）
py -3.14 -m muse.task_cli --db data/tasks/muse.sqlite3 cancel TASK_ID --by user --reason "no longer needed"
# 查看任務隊列統計與活動任務摘要（不輸出 payload）
py -3.14 -m muse.task_cli --db data/tasks/muse.sqlite3 list --limit 20
FFmpeg 任務採人工崩潰恢復策略：若 Worker 租約過期，任務會回到 waiting_approval，先檢查舊程序/輸出，再重新批准；不會自動重複啟動。
FFmpeg 任務採人工崩潰恢復策略：若 Worker 租約過期，任務會回到 waiting_approval，先檢查舊程序/輸出，再重新批准；不會自動重複啟動。

# Agent 管理：只探測明確列出的本機健康端點，不掃描端口、不啟動未知程序
py -3.11 -m muse.agent_discovery --config config/agent-discovery.example.json --db data/agents/muse.sqlite3
# 持續刷新健康狀態；前台運行，Ctrl+C 停止，不會安裝 Windows 服務
py -3.11 -m muse.agent_discovery --config config/agent-discovery.example.json --db data/agents/muse.sqlite3 --loop --interval-seconds 30
# 本地 Agent 调用协议：POST /api/v1/invoke，JSON 请求 {"agent_id":"coder","prompt":"..."}，响应 {"agent_id":"coder","output":"..."}
# 在应用启动时显式注册：router.register_local_http_agent(profile, "http://127.0.0.1:9000/api/v1/invoke")
# 桌面状态 API 可选传入 --agent-db data/agents/muse.sqlite3，读 GET /api/v1/agents
如需从源码目录运行模块：

```powershell
$env:PYTHONPATH = 'src'
python -m muse.main --check
```
## 一键本地视频合成（B-031 第一阶段）

将一个素材文件夹中的图片和视频按文件名自然顺序自动剪辑合成为 MP4：

```powershell
scripts\muse-video.cmd make "D:\Media\素材" "D:\Media\成片.mp4" --audio "D:\Media\bgm.mp3" --audio-mode music --size 1920x1080
```

- 支持图片/视频混合；图片默认每张 4 秒，视频保留原时长。
- `--audio-mode music` 循环背景音乐；`--audio-mode narration` 将旁白补静音至视频结尾。
- `--encoder auto` 优先使用已检测到的 AMD AMF H.264；也可选 `software`（CPU x264）或 `amf`（强制硬件编码）。
- 支持 `1920x1080`、`1080x1920`、`1080x1080`，最终由 FFprobe 检查视频流、尺寸和时长；不覆盖已有输出，不修改源素材。
- 这是已有素材的自动剪辑/合成。另已接入本地 AI 视频产品入口：提示词 → Wan 2.1 / ComfyUI → Remotion 动效包装 → FFmpeg → MP4、工程 ZIP 与 QA 报告；两条制作路径分别记录。
- Wan 2.1 实机验收尚未完成：本机 AMD 首次推理已执行采样，但在 VAE 解码遇到 HIP launch failure；当前改用 tiled VAE + CPU VAE 复验，成功生成并通过 MP4 检查前不会标记完工。详见 `docs/PROGRESS_B128_VIDEO.md`。
- 引擎状态检查：`scripts\muse-video.cmd engines` 只读探测 ComfyUI 与 Velorn MCP；不会启动服务或提交生成任务。
## Windows 日常使用（无需 ChatGPT）

1. 首次安装桌面快捷方式：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Install-Muse-Shortcut.ps1。
2. 双击桌面的 Muse 快捷方式：按需启动已配置的本机模型服务（若存在）、本地 AutonomousSupervisor 和局域网配对控制台，并以 Edge App 窗口打开本机界面。
3. 关闭后台运行：执行 powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Muse-Stop.ps1；暂停/恢复可在项目根目录运行 py -3.11 -m muse.autonomous_runtime pause / resume。
4. 本地日常能力包括队列、知识完整性、能力雷达、Watchdog 和有界自主研究；研究可使用已配置的本机模型与公开来源。ChatGPT 不是运行依赖。网络搜索不可用时不承诺在线研究成功；模型不可用时本地维护任务仍可运行。
5. 此入口是本机浏览器 App 窗口，不是已打包的 Tauri EXE。当前机器没有 Rust/Cargo 工具链，且 Tauri bundle 尚未启用；不会擅自安装工具链。
6. 主电脑必须使用 `http://127.0.0.1:8766/`（桌面快捷方式默认打开此地址），不需要配对；如果主电脑误打开 `192.168.x.x:8766`，会看到给其他设备使用的配对页。手机/平板连接同一 Wi-Fi 后打开桌面 Muse“灵感工坊”显示的局域网地址，首次输入主电脑显示的 8 位配对码。每次 Muse 重启会轮换配对码，设备授权 12 小时。Windows 防火墙只尝试开放 Private Profile + LocalSubnet 的 TCP 8766；若未获权限，需要手动允许该规则。
7. 在“灵感工坊”提交 1–2000 字灵感。默认只写入本地 Research Wiki；勾选“允许联网研究”后，才将灵感加入自主研究候选池并用于公开资料检索。灵感历史在左栏，生成的 Markdown 研究报告在右栏下载。当前流程交付研究报告、假设和待审核改进提案；尚未实现从灵感自动产出可发布产品，也不会自动修改 Muse 代码。视频创作现有独立的本地 AI 提示词入口和素材合成入口；Wan 2.1 的 AMD VAE 解码稳定性仍在实机复验，尚不等于可发布完工；从空白提示词直接生成图片/音频尚未接入。报告原件在 `data/research/reports`，知识记录在 `data/research/knowledge.sqlite3`。
8. 共享服务只绑定私有网络地址，远程设备必须配对；不要在公共 Wi-Fi、访客网络或互联网端口转发环境下使用。HTTP 局域网流量未加密，敏感环境请先加 VPN/HTTPS。
- The two Task Scheduler entries MuseAutonomousRuntime and MuseDesktopDashboard are registered without logon/startup triggers; the desktop shortcut starts them on demand. They run as the current user at Limited privilege, not as Windows services.


## 原则
1. 主动触达必须由明确配置的定时/事件触发器完成，不假设模型会在后台自行思考。
2. 记忆可查看、可导出、可修订；敏感资料默认不收集。
3. 电脑操作按最小权限执行；删除、覆盖、付款、发送消息、部署等动作必须有独立授权。
4. 每项自动化都要有日志、结果状态和失败说明。
5. 不把模型生成的推测写成用户事实。

详见 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)。
