# B346｜Worker/Builder 当前快照交叉验证与 Grant 接线边界

日期：2026-11-22

## 已完成
- 追加验证灵感/研究主链：`tests.test_inspiration_evolution`、`tests.test_research_business_bridge`、`tests.test_autonomous_research` **80 tests / 0 failures / 0 errors**，33.611 秒；内层 psmux Job `4f763b3ab7a84304a4cff0d29130e0e5` 与外层 WebCodex Job `wc_job_zCx0lANJX_MEzuvG` 均 exit 0。覆盖显式商业 opt-in/no-op/无效输入、研究假设入库与幂等、反证检索、研究中断恢复等路径。

- 通过官方 `scripts/muse_dev.py → psmux`、Python 3.11 对 `tests.test_video_task_worker`、`tests.test_video_product_builder`、`tests.test_video_checkpoint_adapter` 执行交叉回归：**101 tests / 0 failures / 0 errors**，15.218 秒；内层 psmux Job `27d1d68eddd64664b78e34d8d638d11a` 与外层 WebCodex Job `wc_job_Blrs-rKIkXCLleAj` 均 exit 0。FFmpeg 对损坏 MP4 负向夹具输出 `moov atom not found`，不影响测试通过。
- 本轮再次对研究/灵感/商业候选相关 5 个模块执行官方 `scripts/muse_dev.py → psmux`、Python 3.11 回归：`tests.test_inspiration_evolution`、`tests.test_research_business_bridge`、`tests.test_autonomous_research`、`tests.test_value_forge`、`tests.test_business_opportunities`，**108 tests / 0 failures / 0 errors**，26.563 秒；内层 psmux Job `889e84cce1684345a521073ffd155a54`、外层 WebCodex Job `wc_job_FlqjFehivnGJUS0m` 均 exit 0。覆盖显式 opt-in、候选/假设证据幂等、反证与中断恢复、商业评分与审批门禁。
- 另对 `tests.test_initiative_engine` + `tests.test_video_studio` 执行官方 psmux/Python 3.11 回归：**60 tests / 0 failures / 0 errors**，5.045 秒；内层 psmux Job `781f30d0a76546d28db95d07c85b069f`，外层 WebCodex 调用同步返回成功。Grant API 的 current-lease/one-shot、租约过期失效、digest/scope/capability/lease mismatch、revoke/expiry 终态，以及 VideoStudio 的能力/角色/handler gate、DAG batching、preview-only 与 checkpoint contract 均通过。
- 最新 `autonomous_research` 回归已通过 `test_creator_candidate_preserves_each_core_symbolic_framework_provenance`，确认四类核心象数框架候选保留各自来源标记；本轮只验证，不改该文件（由研究闭环单写者维护）。
- 复核确认 Worker 多镜头链路已把全局与逐镜头 negative_prompt 合并后传至 Wan，并将逐镜头条件纳入 checkpoint 输入指纹；Builder 在每次 Wan 调用前执行 scene_generation_guard，复核 lease 与最新 Commander approval。Worker 成功路径只在 Attempt complete 成功、回读 succeeded 状态及三项产物 digest 后清理 staging；清理异常仅告警，不反转已提交成功。
- InitiativeStore 已有 Grant create/consume/revoke/expiry API；Grant 绑定 Attempt/worker/lease epoch、recipe/input/DAG/batch、handler/resource/cost digest、approval task/event/actor、scope/role/capabilities，并在 SQLite 事务中一次性消费。
- 新增 `tests/test_video_commander_admission.py`，验证 provider→InitiativeStore 的真实契约：当前 Attempt recipe/approval 绑定、Grant 单次消费、混合角色批次拒绝、无资源证据 fail-closed。首次回归暴露 provider 曾向 Store 传入不支持的 `recipe_sha256` 且遗漏必填 `input_sha256`；已修复 provider：从 Store 当前 Attempt 复核 recipe/approval，创建 Grant 时传入 `input_sha256`。官方 `scripts/muse_dev.py → psmux`、Python 3.11 **4 tests / 0 failures / 0 errors**，0.467 秒，内层 Job `90bceda96e9f42c5905ec8a54a8269ff` exit 0。

## 仍未闭环

- B347 已在生产 CLI `main()` 与 `DaemonControl.run_once()` 构造并注入 `VideoAdmissionRuntime` + request factory；仅在 `MUSE_ENABLE_VIDEO_WORKER=1` 时启用。Registry DB 缺失或 readiness 未通过时，Worker 在领取任务前返回 blocked/no-claim；这不代表 GPU 已获准。
- `build_video_admission_request()` 纯工厂，供 Commander runtime 注入；它绑定 Worker 传入的 plan/batch 与真实 Attempt/approval/input。B347 另加 DAG 来源闸门：只接受 CommanderPlanner 的 `model`/`safe_fallback` source，拒绝已知 Worker/Checkpoint/VideoStudio 本地 plan source；queued video approval 必须带 `commander_plan_id`/`commander_plan_source`，并与 grant request.plan 匹配。已知本地 fallback 不能再取得 Grant；但 Worker 仍需从 Commander 持久化记录恢复 plan，不能自行伪造同名 source，原始 DAG 持久化证明仍未闭环。
- B347 composition root 只打开现有 CapabilityRegistry/AgentRoleRegistry，并将真实 `generate_wan21_t2v` callable 绑定到 `comfyui_generation`；不会自动注册 capability 或 role。Readiness 要求 ready video capability、非 unverified/metadata-only trust、已知 license、restricted 的 `video_studio/developer` 精确单能力授权及完整成本配置。Grant 时还会复核最新人工批准、真实 GPU lease/supervisor coordination、ComfyUI health/空闲队列与 Wan VRAM headroom。
- `build_video_studio_batches` 已按拓扑顺序切分 role-homogeneous chunks，同时遵守容量上限、保留跨批 DAG 前置依赖；role 进入 batch fingerprint，`VideoStudioBatch` 暴露 `role` / `required_capabilities`，checkpoint key 绑定完整 DAG + batch SHA-256。`tests/test_video_studio.py` 当前 **25/25 PASS**；该文件由独立 VideoStudio owner 复核接管。
- 剩余生产闭环：Worker/Checkpoint adapter 必须要求 Commander 签发并持久化的原始 DAG provenance；由授权流程显式登记真实 `comfyui_generation` capability 与 restricted scoped role；配置本地整机功耗、电价、单次预算及最大运行时间；随后才可进行真实 ComfyUI/Wan GPU smoke test。
## 本轮 Worker 修复与复验（2026-11-22）
- 修复 `video_task_worker.py` 中 Grant 消费方法对 `TaskQueue` 的作用域导入错误；将正式导入提升到模块作用域，避免多镜头/授权撤销/Checkpoint 恢复路径出现 `NameError`。
- 统一租约丢失且无法释放旧 Attempt 时的结果字段，明确返回 `failure_recorded=false`，不把已被新 Epoch 接管的 Attempt 标记为失败。
- 修正 Worker 专项测试的 SQLite 查询：使用 `InitiativeStore.connection` 公共连接属性，并查询正式表 `initiative_video_admission_grants`。
- 官方 `scripts/muse_dev.py → psmux`、Python 3.11：`tests/test_video_task_worker.py` **34/34 PASS**（内层 Job `921212cc5d1d4663b4cbabd8b89c2251`，exit 0）；`tests/test_video_worker_reverification.py` **2/2 PASS**（内层 Job `442b4c39fd6f4f94b4ed068d9c4a7b2c`，exit 0）。Worker 与其直接测试文件 `py_compile` PASS。
- Worker/Admission 最新专项：`tests/test_video_task_worker.py` **35/35 PASS**（内层 psmux Job `ef14a0c0382540a5ace8014812961f05`）；`tests/test_video_commander_admission.py` **6/6 PASS**（内层 Job `1645222a71e2473bb87960030b1e99ef`）。新增 `build_video_admission_request()` 纯工厂，只从 Worker 传入的原始 CommanderPlan 与官方 singleton batch 构造 request；严格校验 batch 对象身份、单 step、无未完成依赖、role/capabilities、Attempt/lease/recipe/input/approval，不伪造 DAG 或 digest。Worker 另验证 factory 返回的 plan/batch/all_batches/handler/workspace/input digest 与 Worker 原始对象完全一致。
- 修复多镜头旁白分支的 DAG 缺失：即使启用 Cantonese narration（因此 checkpoint 暂不启用），也会先建立完整多镜头 Commander DAG，再为每个镜头派生官方 singleton batch；新增端到端 Worker 回归，确保两个镜头使用同一 DAG hash、各自独立 batch fingerprint 与一次性 Grant。
- 视频跨链专项 **141 tests / 0 failures / 0 errors**：Worker 35、Builder 63、Checkpoint adapter 10、Admission provider/factory 6、VideoStudio 25、Worker reverification 2；官方 psmux 外层 Job `wc_job_MXZLlFt1DTtNwsTr`、内层 Job `3207ad49d1f24d5882e6c35332740936` 均 exit 0，15.441 秒。FFmpeg `moov atom not found` 来自损坏 MP4 负向夹具，测试通过。
- 全仓 Python 3.11 `compileall -q src/muse tests scripts` PASS。最新官方 `scripts/muse_dev.py → psmux` 全量 `unittest discover`：**2,322 tests / 0 failures / 0 errors / 1 skipped**，217.952 秒；外层 Job `wc_job_d1zFqRI7IdEAXB0S`、内层 psmux Job `d197fba246d84d69b68402254ceecc94` 均 exit 0。日志中的 FFmpeg、缺失图片、mock provider 与 CLI 参数错误均为既有负向测试的预期输出，不构成失败。

## 2026-11-23｜B358 Wan dispatch admission callback 接通

- 代码审查发现真实断链：`VideoProductBuilder` 已传入 `admission_guard`，但 `generate_wan21_t2v()` 原签名不接收该参数，真实 Wan dispatch 会在提交前因 unexpected keyword argument 中断。新增专项测试覆盖真实 lease 回调顺序、拒绝路径及无 lease fail-closed。
- 修复 `src/muse/wan_video.py`：公开函数接收可选 `admission_guard`；提供 guard 时必须提供 `resource_db`，并且只在 `GpuLeaseSession` 已进入后执行；先通过真实 VRAM headroom 检查，再将同一个 live lease 对象传给 guard，guard 返回后再次验证 lease，最后才允许 `submit_workflow`。拒绝异常原样传播，确保 Worker 的 `VideoAdmissionBlocked` 仍可被识别；没有 lease 或 guard 拒绝时不会提交 ComfyUI workflow。
- 定向验证：`tests/test_wan_video.py` **18/18 PASS**；全部 `test_video*.py` **359/359 PASS**，包含 VideoProductBuilder、VideoTaskWorker、VideoStudio、Checkpoint、QA 与发布包测试；`compileall -q src/muse` exit 0。全仓全量回归在新增第 18 项单测前为 **2,452 tests / 0 failures / 0 errors / OK**；新增单测随后由 Wan 专项 18/18 覆盖。全程没有启动 ComfyUI、Wan 或真实 GPU 渲染。
- B347 的生产 Provider/factory 接线已完成并通过定向验证；剩余 blocker 是真实 capability/role/cost 配置、Commander DAG provenance 与真实 GPU 验收。中间一次视频专项快照因并行 Session 短暂写入 `autonomous_research.py` 时出现 IndentationError，owner 恢复后稳定重跑已通过。未执行真实视频生成或自动发布。

