# B391 · 路径 B（L5）实机作战卡 — ComfyUI + Wan + 人工审批

> 适用：`3_双击B391验收.bat` 已通过（L3/L4），现在要在你的 Windows 上验收 AI 镜头路径。
> 本卡只描述操作与观察点；任何一步都不会自动批准或自动启动 GPU。证据层级：完成本卡并拿到成片 = L5。

## 0. 前置条件（缺一项就停在这里）
| 检查 | 期望 | 在哪里看 |
|---|---|---|
| ComfyUI 已启动 | `http://127.0.0.1:8188` 能打开 | 浏览器；或验收报告 `reports/b391_acceptance/LATEST_REPORT.md` →「环境快照 · ComfyUI :8188 = 是」 |
| Wan 工作流 / 模型 | ComfyUI 左侧能加载 Wan 工作流且无红色缺失节点 | ComfyUI 界面 |
| GPU 空闲 | `/os` → 设置 → GPU WAL 租约 `status: idle` | Super Agent OS |
| 路径判定 | `/os` → 工具箱顶部显示「当前自动选择：AI 镜头路径」；否则看列出的缺失项 | Super Agent OS |
| Worker 开关 | 运行 Worker 的那个窗口里设置了 `MUSE_ENABLE_VIDEO_WORKER=1` | 见第 3 步 |

## 1. 端口探测确认点
1. 打开 `http://127.0.0.1:8188` —— 能看到 ComfyUI 画布。
2. 打开 Muse 控制台 `/os` → 工具箱：「AI 镜头生成（Commander 审批 + Video Admission + GPU 租约 + Wan/ComfyUI）」应为「就绪」。
   如显示缺少 `comfyui_ready` / `commander_planner_ready` / `gpu_idle`，先补齐对应项（这是真实探测结果，不是界面猜测）。

## 2. OS 工具箱提单参数
`/os` → 工具箱 → 智能创作入口：
- 影片名称：自定（≤120 字）
- 制作路径：选「AI 镜头（Wan · 需 Commander 审批）」（或保持「智能自动」且第 1 步判定为 AI 路径）
- AI 画面描述：必填（≤1800 字）
- 粤语旁白稿：可选
- 固定参数（界面写死，与后端一致）：`aspect_ratio=16:9`、`duration_seconds=2`、`steps=20`
- 点「开始制作」→ 进度框显示「Commander 一次性审批票：<task_id> · 批准前不会启动 Wan 或占用 GPU」。

## 3. 人工批准位置（唯一入口）
`/os` → 任务中心 → 「待审批门禁操作台」→「Video Admission · 一次性 AI 镜头授权」→ 找到刚才的条目 →
「批准这一次 AI 镜头生成」→ 弹窗中确认短语 `APPROVE-VIDEO-ONCE`（可点“一键填入”，**仍需你点「确认提交」**）。
- 通用「批准 Ticket」按钮对视频票一律 409 拒绝并提示专用门禁，这是设计如此。
- 批准后只会生成一张 `video_pipeline` 执行 Ticket（排队中），GPU 仍未启动。

## 4. Worker 消费观察点
在项目根目录打开一个新的 PowerShell 窗口（路径含空格，务必带引号）：
```powershell
cd "X:\PATH\TO\Muse"
$env:MUSE_ENABLE_VIDEO_WORKER = "1"
$env:PYTHONUTF8 = "1"
.\.venv\Scripts\python.exe -m muse.autonomous_runtime once
```
（需要持续消费时把 `once` 换成 `run`，Ctrl+C 结束。若 .venv 损坏，用 `py -3.11 -m muse.autonomous_runtime once` 并先设置 `$env:PYTHONPATH="X:\PATH\TO\Muse\src"`。）
观察：
- Worker 是自治运行时里的例程 `creator-video-queue`（kind `video_worker`，每 300 秒到期一次）。
  `once` 只跑一个 tick：如果输出里该例程显示未到期，改用 `run` 持续运行（最多约 5 分钟会轮到它）。
- 例程结果：`claimed: true` 表示领到了批准过的执行票；`status: disabled` 表示没设 `MUSE_ENABLE_VIDEO_WORKER=1`；
  其余状态会写明原因（如 GPU 忙、Admission 未就绪）。
- `/os` → 工作流 →「视频审批与执行 Ticket」：执行状态应从 `queued` → `running` → `succeeded/failed`。
- `/os` → 设置 → GPU WAL 租约：运行期间出现 lease，结束后释放回 `idle`。
- 成功后 `/os` → 工具箱 → 成片库出现新作品，可播放（支持拖动进度条，Range 206），卡片显示 MP4 SHA-256。

## 5. 失败日志路径
| 现象 | 去看 |
|---|---|
| 提单 4xx/5xx | 弹窗/提示里的 `HTTP <状态码> · 后端原因`（原样来自后端） |
| Worker 未领票 | 第 4 步命令的 JSON 输出（`video_worker.status` / `reason`） |
| 执行失败 | `/os` → 任务中心 → 该执行 Ticket「详情」→ 事件时间线（`error_type` / `error_code`）；`/os` → 工作流 → 产能账本 |
| GPU 未释放 | `/os` → 设置 → GPU WAL 租约 + `model_supervisor` 字段 |
| ComfyUI 端报错 | ComfyUI 自身控制台窗口 |
| 总体回执 | 对 AWS Quick 说“看验收报告”，并附上第 4 步输出 |

## 6. 验收判定
- ✅ L5 通过：执行 Ticket `succeeded` + 成片库出现新作品 + 播放正常 + SHA-256 可见 + GPU 租约已释放。
- ❌ 任一步失败：不要重复点击批准（每次批准都是新的一次性授权）；把失败点对应的日志发给 AWS Quick。
