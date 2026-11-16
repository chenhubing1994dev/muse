# B135 — GPU 资源协调与安全恢复

日期：2026-11-14

## 本轮目标

让 Muse 的 Wan/ComfyUI 视频任务与 Muse 自己管理的 llama.cpp 模型服务共享 AMD GPU 时，不再靠人工猜测显存或反复尝试启动。目标是一个跨进程、可过期回收、在外部应用边界内 fail-closed 的 GPU 资源租约；它不是第二个任务调度器。

## 已实现

1. 新增 `src/muse/gpu_resources.py`：
   - SQLite WAL + `BEGIN IMMEDIATE` 串行化跨进程 GPU 独占租约。
   - 支持 video_generation / model_inference 两种资源所有者、心跳续租、显式释放、TTL 过期回收和有限事件审计。
   - Supervisor 写入心跳、动作、当前租约确认与 restore_pending；Dashboard 只读数据不暴露租约 ID、owner_id、PID 或数据库路径。
   - 自定义 SQLite connection context 显式关闭连接，避免 Windows 文件句柄残留。

2. 模型 Supervisor：
   - `start_if_needed` 在视频租约存在时拒绝启动模型。
   - 只有 Supervisor 自己通过 `Popen` 创建并持有引用的 llama-server 子进程才可能在视频租约期间被终止；外部模型端口健康但没有本进程所有权时，报告 external_model_active 并拒绝抢占。
   - 在恢复模型前读取 ComfyUI `/system_stats` 的设备显存与 Torch 显存池。当前策略要求设备与 Torch 池各自至少 70% 可用；计数缺失时不猜测，等待后再检查。
   - 显存不足/未知属于等待状态，不消耗每小时模型重启额度。

3. Wan/ComfyUI：
   - `VideoProductBuilder` 的 Wan 生产入口传入工作区 GPU 资源数据库。
   - Wan 开始前获得 GPU 独占租约；生成成功后读取 ComfyUI 队列。只有 running/pending 均为 0 才调用官方本地 `POST /free` 的 `unload_models/free_memory` 标志。
   - 请求后持续查询显存；只有实测设备显存达到 70% 空闲，才记录释放已确认。队列繁忙会延迟释放；未知/不可用/未达标则记为未确认。
   - 显存释放状态写入资源事件，并通过生成结果与 Dashboard 展示。

4. 控制台：
   - 新增认证只读 API `GET /api/v1/gpu-resources`。
   - 总览显示 GPU 空闲/占用、模型 Supervisor 状态、ComfyUI 显存释放状态。

## 测试与实机证据

- B135 后端专项回归：82/82 通过，覆盖租约互斥/过期恢复、Supervisor 让位与恢复门槛、ComfyUI 显存解析和 `/free` 参数、Wan 资源租约、队列繁忙跳过卸载、外部模型冲突保护、状态 API 脱敏。
- 全项目 Python 全量回归：**1,164/1,164 通过**（125.894 秒，exit code 0），在增加外部模型端口检测和 Supervisor 状态测试后重新全量执行。
- Dashboard Node 测试：25/25 通过；Svelte 检查：0 errors / 0 warnings；Vite 生产构建通过；Remotion TypeScript 检查通过。
- `compileall src/muse tests scripts` 与 `pyproject.toml` TOML 解析通过。
- 当 ComfyUI 8188 曾在线时，RX 6750 GRE 实测设备 VRAM 总量 12,868,124,672 bytes、可用 2,612,104,192 bytes（20.3%）；Torch 池总量 10,122,952,704 bytes、可用 261,983,232 bytes；队列 0 running / 0 pending。随后复核时 ComfyUI 已停止，8188 不再监听，不能把之前的计数当作当前状态。
- 发现并修正一个实机安全缺口：ComfyUI 探测失败不能直接等同于“GPU 空闲”。现在 Supervisor 先检查 LM Studio 1234 与 ComfyUI 8188 的 loopback 端口；LM Studio 在线时返回 `external_model_active` 并禁止启动第二个模型；ComfyUI 在线但显存接口失败时返回 `unknown` 并 fail-closed。
- 通过确切 GPU 租约让 Supervisor 自己停止其拥有的 llama-server PID 155416；确认 1235 关闭后，停止旧 Supervisor 循环并用修复版重新启动计划任务。停止的是 Muse Supervisor 自己创建并管理的模型子进程；LM Studio 1234 与其他外部程序未被停止或修改。
- 当前实机状态：MuseLocalModelSupervisor 计划任务 Running，Supervisor 心跳在线，动作 `external_model_active`，`restore_pending=true`，`managed_child=false`；LM Studio 1234 正在监听，Muse llama.cpp 1235 已关闭，ComfyUI 8188 当前未运行。释放租约后直接调用 `start_if_needed` 得到 `external_model_active`，没有创建第二个模型进程。

## 仍未宣称完成的部分

- 没有对 ComfyUI 直接发出 `/free`：现场复核时 ComfyUI 已停止，且不能为了测试擅自卸载用户模型。释放接口与实测轮询由模拟测试覆盖，实际视频生成任务中的卸载/显存确认/模型恢复还要做端到端验收。
- 自动恢复守护现已运行，但目前由外部 LM Studio 1234 占用模型服务位，Muse 的模型服务处于安全等待状态；需在 LM Studio 关闭或切换为单一模型提供方后，验证 Muse llama.cpp 自动恢复。本轮没有停止或更改 LM Studio。
- 当前显存策略是设备及 Torch 池都需达到 70% 空闲；不能据此承诺两种模型可同时驻留，也不假设其他未登记 GPU 进程不存在。
- GPU 租约不取代视频 QA、版权/肖像/AI 披露审核、多平台发布授权、平台数据回读或收益账本。

## 下一步

1. 在不干扰 LM Studio 的前提下，完成真实 Muse 视频任务的租约 → ComfyUI 生成 → 队列空闲卸载 → VRAM 实测确认闭环。
2. 在用户选择/提供单一 GPU 模型时段后，验证 Supervisor 自动恢复 llama.cpp、健康检查和再次让位；不擅自关闭 LM Studio。
3. 将音频/旁白/字幕/多镜头连续性、技术与内容 QA 接到统一产品流水账，再做发布授权与平台数据/收益回流。
4. 持续扩展资源画像、公平队列与故障恢复，并按真实测试结果更新进度。

本轮交付是带外部服务识别的 GPU 资源协调与可验证安全门槛，不等于视频商业化闭环或 ComfyUI/llama.cpp 实机切换验收已全部完工。
