# B-141 自主运行时控制台 + 一键创作流水线

日期：2026-11-17
项目：D:\AI AGENT\Muse

## 本轮交付

### 1. 自主运行时（24h 常驻）受控接入
- 新增 `src/muse/daemon_control.py::MuseDaemonController`：包装现有权威的 `muse.autonomous_runtime run` 子系统，提供 start / pause / resume / stop / once 生命周期控制。
  - 复用 RuntimeStateStore 的 SQLite 单例租约，不绕过 Commander；子进程由控制器以 PID 租约记录（data/autonomy/runtime.lease.json）。
  - 所有控制动作追加写入 `data/autonomy/control-log.jsonl`（有界 64 KiB / 200 行滚动），可审计。
  - `stop()` 在观察 stopping 租约的同时，对托管 PID 发送终止信号，并把状态收敛到 stopped、清理 owner，避免长 tick 导致停止失效（冒烟测试发现并修复的真实问题）。
  - `poll_seconds` 限定 1–60；启动仅用 allowlist CLI 参数，无任意命令执行。
- status_api 新增端点：`GET /api/v1/daemon/status`，`POST /api/v1/daemon/{start,pause,resume,stop,once}`。

### 2. 一键创作流水线
- `src/muse/pipeline_control.py::PipelineController` 仅执行 `high_value_short_v2`；粤语 TTS/ASR 试验配方在 UI/API 标记为暂不可用，直至真实粤语语音生成与识别验收接通。所有任务写入唯一 `data/tasks/muse.sqlite3` TaskQueue，不自建 JSON job ledger 或后台调度线程。
  - 由既有 `AutonomousSupervisor`/Worker handler 执行配方脚本，Worker 租约、心跳、失败分类和恢复策略统一生效；任务设置 `workload_class=media`、`estimated_seconds=1800`、`recovery_policy=manual`，过期不自动重复执行。
  - 仅接受 recipe allowlist；每个任务在 `data/prometheus/runs/<recipe>/<run-id>` 获得独立输出目录，旧成片永不覆盖；路径必须留在 workspace，30 分钟超时；任务状态与结果从同一 TaskQueue 读取。
- status_api 新增：`GET /api/v1/pipelines/{recipes,jobs,job/<id>}`，`POST /api/v1/pipelines/submit`。

### 3. 前端面板
- `src/App.svelte` 新增「自主运行时」面板：状态/心跳/托管PID/错误展示，启动/暂停/恢复/停止/只跑一轮五个按钮，全部走鉴权 API。
- 新增「一键创作流水线」面板：配方下拉、运行按钮、任务列表与进度轮询（终态自动停止轮询）。
- 左侧导航与 Ctrl+K 命令面板加入两个入口；`lib/api.ts` 补齐类型，`api.js` 补齐客户端方法。
- 新增 `ui-prototype/api.test.js` 中 2 项端点测试。

## 验证证据
- Python：B-142 原始全量回归 **1261/1261** 通过；本轮追加后的 `test_pipeline_control.py` **10/10**、`test_status_api.py` **28/28** 通过。新测试覆盖配方可用性、任务输出目录/标题传递、MP4/ZIP 文件存在性门禁及 HTTP 提交到 Commander TaskQueue。
- 端到端 API：daemon status/recipes/jobs GET 正常；daemon pause/resume POST 正常；非法 recipe 返回 409。
- 守护进程冒烟：start→8 秒后 running(heartbeat=2s)→stop→立即 stopped、无残留子进程。修复前 stop 后进程仍存活 42 秒，已修正。
- 前端：svelte-check 0 errors / 0 warnings；npm test 36/36；Vite build 成功（119 modules，CSS 40.76 kB / JS 146.15 kB）。
- 桌面：重新构建并部署 `desktop/src-tauri/target/release/muse-desktop.exe`，8,696,320 bytes，SHA-256 `3AD93D992EF0314C5C39D9ABD96078DBE95EDB1C2D5D7766C1A0BEB357A4DACB`；窗口 `MUSE · 灵枢调度中心` Responding=True；后端命令行含全部数据路径参数。
- 前端 dist 产物确认包含 `daemon-status` / `pipeline-recipe` / `自主运行时` / `high_value_short_v2` 等新代码字符串。
- 架构修正（B-142）：将创作流水线从私有 JSON ledger + `threading.Thread` 改为 Commander TaskQueue ticket + 现有 Worker handler；AutonomousSupervisor 只允许领取 `local_workflow` 与 `video_pipeline` 两类本地任务，避免离线 Worker 越权领取外部任务。
- 队列历史查询新增按 payload kind 的 SQLite JSON 索引过滤语句，避免大量其他任务挤掉创作历史；8 项管线测试已通过。
- B-143 出片契约修正：Worker 将 recipe output/title 传入 `MUSE_PIPELINE_OUTPUT_DIR` / `MUSE_PIPELINE_TITLE`；脚本按任务参数写入目标目录，校验输出仍位于 workspace。Handler 只有在结果 JSON 指向输出目录内的非空 MP4 与 delivery ZIP 时才接受 `completed`。
- B-144 输出隔离：实机检查发现固定目标 `data/prometheus/high-value-short-v2` 已有旧成片，因此新增每任务 UUID 输出目录并写入 Commander Ticket；Handler 仅接受 Ticket 指定的安全相对路径，避免覆盖历史作品，也允许连续重复生产。
- 粤语配方未接通前明确禁用，前端下拉项显示原因并禁止提交；失败任务的 `error` 字段从 TaskQueue 持久化 `_result` 中提取安全错误码/信息，避免 UI 永远显示“未知错误”。

## 边界与说明
- 交付时运行时保持 stopped：常驻服务需用户在界面显式启动，不在后台擅自运行。
- GPU 视频派发默认关闭（MUSE_ENABLE_VIDEO_WORKER），运行时不自动批准代码、不自动发布、不使用付费 API。
- 流水线产物状态为 testing，需人工审片；配方产出仍复用已验证镜头，非任意新题材生成。
- 视觉截图验收未完成（Playwright 未安装、Tauri WebView 无法被 Playwright 直接截图），以 DOM/构建/API 证据替代。
