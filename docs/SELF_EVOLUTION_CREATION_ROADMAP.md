# Muse 自主研究、进化与创作闭环推进记录

更新时间：2026-11-15

## 本轮验证

- OpenFilm Desktop 安装位置已确认：`D:\\Program Files\\OpenFilm\\OpenFilm.exe`；内置 CLI 报告版本 0.3.2。
- OpenFilm 自动化 `look` 调用尚未完成实机验证：Studio 启动/连接阶段长时间无输出。不得标记 `look` 或 `render` 已通过。
- 加固 `src/muse/openfilm_adapter.py`：素材复制及 SHA-256、标题 iframe 分轨、footage 同轨重叠拒绝、每条音轨独立 section、audio kind manifest；不覆盖既有非空工程。
- 研究 Brief v1：研究周期输出来源/证据状态、视频提示词、粤语旁白目标、离线产品需求及自动发布/事实晋升边界；高证据才排视频任务，低证据只保留研究结果。
- 新增 `src/muse/video_task_worker.py` 与 `muse-video-worker` CLI：每次最多领取一项 `prompt_to_video`，读取 Creator Brief，调用现有 Wan 2.1 VideoProductBuilder，验证产物后回写视频任务账本。
- `InitiativeStore` 增加 SQLite `BEGIN IMMEDIATE` 原子领取、worker lease、过期 lease 恢复、领取次数、排队取消、worker 主动释放、显式失败重试及 append-only 事件记录；Status API 旧执行路径也写入可恢复 lease；不会启动多个 GPU 生成任务。
- Worker 启动前及领取后都会检查运行时 pause/stop；暂停时任务退回队列。GPU 独占租约仍由 `wan_video.py` 执行，Worker 不停止外部模型服务。
- Worker 固定使用 attempt ID 派生 seed，重试保持生成输入确定；失败不会自动无限重试，需显式 `--retry-attempt`，默认最多 2 次。
- Worker 只生成并打包产品，不自动发布；产物仍要求人工内容审片。当前是单次 CLI Worker，不会自行常驻或自动消耗 GPU。
- 本轮把 Worker 接入 `AutonomousSupervisor` 的 Commander routine `creator-video-queue`：每轮最多调用一次 Worker；默认 `MUSE_ENABLE_VIDEO_WORKER` 未设为 `1` 时只回报 disabled，不领取任务、不启动 GPU。运行时 pause/stop 与 Worker 自身检查保持有效。
- Status API `/api/v1/video-attempts` 增加 queued/running/leased 指标，并公开 worker ID、lease 到期时间、领取次数及取消请求状态，供 Dashboard 展示和排障。
- 本轮聚焦回归 99 项通过：`tests.test_autonomous_runtime`、`tests.test_status_api`、`tests.test_autonomous_research`、`tests.test_video_task_worker`。全仓库发现 1192 项测试，其中 42 项报错、1 项跳过；至少一类失败由当前 Runner Python 环境缺少 `tzdata` 导致 `ZoneInfo('Asia/Shanghai')` 无法加载，未将全仓库回归标记为通过。本轮未启动 Wan/GPU 实机生成。- 最终联合回归：研究、Runtime、本地产品、OpenFilm Adapter、Initiative Store、VideoProductBuilder、VideoTaskWorker 共 113/113 通过（17.808 秒）；`python -m muse.video_task_worker --help` CLI smoke 通过。

## 下一优先级

1. 将研究产出的 Creator Brief 与现有 `story_bible.py`、`video_workflow.py` 接通：持久化 Story Bible、镜头级 storyboard 和 DAG；Worker 逐镜头领取并回写产物，最后拼接，避免把单段预览冒充成片。
2. 为 Commander video routine 增加显式运行配置/状态展示与资源预检（ComfyUI、模型、磁盘、GPU lease）；保持默认关闭，只有用户明确启用后才消费 GPU。
3. 完成 Wan/ComfyUI 实机 smoke：确认模型、GPU 余量、独占租约、MP4、QA 报告与 ZIP；失败要可恢复。
4. 完成粤语 TTS 实机评估、旁白生成、音轨混音与口型/节奏检查。
5. 完成 OpenFilm Studio 导入 smoke project、`look`、`render`、用户编辑回写和交付 ZIP；再接人工审片与发布审核。## 验收原则

研究完成、产品构建、视频渲染、技术 QA、内容审片、版权/肖像/披露审查及平台发布是不同状态。不能以测试通过或生成 MP4 宣称影片合格或可发布。