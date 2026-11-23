# B347｜生产视频 Admission Runtime 接线与 fail-closed 验收

日期：2026-11-23

## 实际修改

- 新增 `src/muse/video_admission_runtime.py`：生产组合根打开现有 `CapabilityRegistry` 与 `AgentRoleRegistry`，将真实本地 `generate_wan21_t2v` callable 绑定到 `comfyui_generation`，并构造 `RuntimeVideoAdmissionProvider` 与 request factory。没有自动创建 capability 记录、角色授权、审批或成本默认值。request factory 只接受 CommanderPlanner `model`/`safe_fallback` 来源，拒绝 Worker/Checkpoint/VideoStudio 本地 fallback。
- Capability/Role readiness 要求：ready 的 video-domain `comfyui_generation` 记录；trust 不得为 unverified/metadata_only，license 必须明确；`video_studio/developer` 必须是 restricted trust tier，且只允许 `comfyui_generation`。
- Cost gate 只接受操作者显式配置：`MUSE_VIDEO_SYSTEM_POWER_WATTS`、`MUSE_ELECTRICITY_USD_PER_KWH`、`MUSE_VIDEO_COST_CAP_USD`、`MUSE_VIDEO_MAX_RUNTIME_SECONDS`。最大运行时间不得低于当前 Worker 1800 秒 timeout；按配置的整机最大功耗 × 最大运行时间计算保守电费上界。缺任一值、输入无效或超过预算即拒绝。该数值是电费估算，不冒充实测成本。
- Human gate 重新读取 TaskQueue 中该 Attempt 的审批 ticket 与最新 `human.decision` event，绑定 approval task/event/actor/attempt/timestamp，并要求 ticket 的 `commander_plan_id`/`commander_plan_source` 与 request.plan 完全一致；新决策覆盖旧审批时拒绝。
- Resource gate 核对当前 `GpuResourceCoordinator` lease id/owner/kind、heartbeat/expiry、supervisor 对当前 lease 的 fresh acknowledgement 或已确认的 supervisor-offline coordination；并要求 ComfyUI health ready、队列无 running/pending jobs、Wan 原有 VRAM headroom 检查通过。
- `AutonomousSupervisor._video_worker` 在 provider/request factory 缺失或 readiness 不通过时，于领取任务前返回 blocked/no-claim；readiness 扫描 bounded TaskQueue 中 queued/waiting_approval/running 的 active video approval，只有明确 approved 且 status=queued、带有 `commander_plan_id`/`commander_plan_source` 才继续；未批准、等待审批、正在运行或缺少 plan provenance 均在领取前阻断。request factory 只接受 CommanderPlanner 的 `model`/`safe_fallback` source，拒绝 Worker/Checkpoint/VideoStudio 本地 fallback；grant wrapper 再次核验 plan source、id 与 readiness。
- 生产 CLI `main()` 与 `MuseDaemonController.run_once()` 已注入该 runtime；one-shot 路径会关闭 registry 连接，长驻路径在进程生命周期内持有并在退出时关闭。

## 验证

- Python 3.11 `compileall -q src/muse`：PASS。
- Admission Runtime 专项：`test_video_admission_runtime` **13/13 PASS**；此前组合定向回归（包含 `test_autonomous_runtime`、`test_daemon_control`、`test_video_commander_admission`、`test_video_studio`、`test_wan_video`）为 **102 tests / 0 failures / 0 errors**。
- 加入 Commander DAG provenance gate 后，全部 `test_video*.py`：**372 tests / 0 failures / 0 errors**；Python 3.11 `compileall -q src/muse`：PASS。
- 测试覆盖 missing provider no-claim、readiness blocked no-claim、显式成本策略、过预算拒绝、最新人工决策、lease/supervisor 精确绑定、ComfyUI idle/VRAM gate、Daemon 注入与资源清理。
- 没有启动 ComfyUI、Wan 或真实 GPU 渲染；目前真实 registry 中仍缺 ready `comfyui_generation` 记录、restricted role grant 与成本环境配置，因此生产 readiness 会 fail-closed。

## 运行状态与历史失败审计

- 只读 CLI 状态显示 Autonomous Runtime 为 stopped，最后 heartbeat 为 2026-11-16；本轮没有擅自启动长驻后台进程。
- `idle-autonomous-research` 的 routine lease 已过期（2026-11-16）；`RoutineStore.claim_due()` 会重新领取 `lease_until <= now` 的 due routine，下一次 Supervisor tick 可恢复，不需要伪造状态或清空 lease。
- TaskQueue 有 38 个历史 failed tasks：2026-11-04 kind=None 23 项、2026-11-06 kind=None 11 项、2026-11-04 `commander_ticket` 3 项、2026-11-17 `video_pipeline` 1 项。2026-11-01 后仅 1 项有结构化 failure classification：`handler.unexpected` / non-retryable；对应 task `8b8ff67ca71246f4af72073c7783167b`，`recovery_policy=manual`。没有自动重试旧任务。
- 全仓回归最新快照：**2,471 tests / 1 failure**。唯一失败仍是 Plugin Platform 的 `test_running_plugin_ticket_rejects_lease_extension_without_matching_heartbeat_audit`（无审计地延长 lease expiry 时未抛出预期 `PluginError`）；该失败已由 TaskQueue/PluginPlatform owner 确认接手 B381，当前主会话不修改其文件，也不与其并行运行全仓测试。

## 尚未闭环

1. Worker/Checkpoint adapter 仍需从 Commander 持久化记录恢复 plan，并将真实 plan_id/source 写入审批 ticket；runtime 已拒绝已知本地 fallback source，但 source 字段本身不是持久化证明。
2. 必须通过授权流程登记真实 ready `comfyui_generation` capability 与 restricted `video_studio/developer` role。
3. 必须由操作者配置上述四项本地成本变量；缺配置时保持拒绝。
4. 等 Plugin Platform owner 修复 heartbeat audit 一致性后，重跑全仓回归。
5. 完成上述项目后才进行真实 ComfyUI/Wan GPU smoke test；当前不宣称真实视频生成已验收。
