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

- `AutonomousRuntime` 已接受成对的 `video_admission_provider` + `video_admission_request_factory` 并透传给 Worker；未注入时仍 fail-closed。生产 CLI `main()` 与 `DaemonControl.run_once()` 仍未构造/透传真实 Provider/factory，因此默认启动仍不具备 GPU execution eligibility。
- 已新增 `build_video_admission_request()` 纯工厂，供 Commander runtime 注入；它使用 Worker 提供的原始 CommanderPlan、官方 batch 对象及真实 Attempt/approval/input，不自造 DAG/digest、不批准资源/成本/人工审批。
- `RuntimeVideoAdmissionProvider` 仍必须由 Commander 注入真实 CapabilityRegistry、scoped AgentRoleRegistry、可信 handler registry，以及可审计的 resource/cost/human gates。GPU resource gate 需查真实 VRAM 与 `GpuResourceCoordinator` lease/supervisor 状态；Worker/Builder 还未在 Wan dispatch 生命周期持有 `GpuLeaseSession`，故当前仍不能宣称消除了资源竞态。
- `build_video_studio_batches` 已按拓扑顺序切分 role-homogeneous chunks，同时遵守容量上限、保留跨批 DAG 前置依赖；role 进入 batch fingerprint，`VideoStudioBatch` 暴露 `role` / `required_capabilities`，checkpoint key 绑定完整 DAG + batch SHA-256。`tests/test_video_studio.py` 当前 **25/25 PASS**；该文件由独立 VideoStudio owner 复核接管。
- 剩余生产闭环：runtime/CLI owner 构造真实 registry + trusted handler + resource/cost/human gates 并注入 Provider/factory；为 Worker/Builder 持有 GPU lease；明确本地 GPU 成本预算/估算策略。缺少 VRAM、GPU lease 或成本证据时必须 fail-closed。
## 本轮 Worker 修复与复验（2026-11-22）
- 修复 `video_task_worker.py` 中 Grant 消费方法对 `TaskQueue` 的作用域导入错误；将正式导入提升到模块作用域，避免多镜头/授权撤销/Checkpoint 恢复路径出现 `NameError`。
- 统一租约丢失且无法释放旧 Attempt 时的结果字段，明确返回 `failure_recorded=false`，不把已被新 Epoch 接管的 Attempt 标记为失败。
- 修正 Worker 专项测试的 SQLite 查询：使用 `InitiativeStore.connection` 公共连接属性，并查询正式表 `initiative_video_admission_grants`。
- 官方 `scripts/muse_dev.py → psmux`、Python 3.11：`tests/test_video_task_worker.py` **34/34 PASS**（内层 Job `921212cc5d1d4663b4cbabd8b89c2251`，exit 0）；`tests/test_video_worker_reverification.py` **2/2 PASS**（内层 Job `442b4c39fd6f4f94b4ed068d9c4a7b2c`，exit 0）。Worker 与其直接测试文件 `py_compile` PASS。
- Worker/Admission 最新专项：`tests/test_video_task_worker.py` **35/35 PASS**（内层 psmux Job `ef14a0c0382540a5ace8014812961f05`）；`tests/test_video_commander_admission.py` **6/6 PASS**（内层 Job `1645222a71e2473bb87960030b1e99ef`）。新增 `build_video_admission_request()` 纯工厂，只从 Worker 传入的原始 CommanderPlan 与官方 singleton batch 构造 request；严格校验 batch 对象身份、单 step、无未完成依赖、role/capabilities、Attempt/lease/recipe/input/approval，不伪造 DAG 或 digest。Worker 另验证 factory 返回的 plan/batch/all_batches/handler/workspace/input digest 与 Worker 原始对象完全一致。
- 修复多镜头旁白分支的 DAG 缺失：即使启用 Cantonese narration（因此 checkpoint 暂不启用），也会先建立完整多镜头 Commander DAG，再为每个镜头派生官方 singleton batch；新增端到端 Worker 回归，确保两个镜头使用同一 DAG hash、各自独立 batch fingerprint 与一次性 Grant。
- 视频跨链专项 **141 tests / 0 failures / 0 errors**：Worker 35、Builder 63、Checkpoint adapter 10、Admission provider/factory 6、VideoStudio 25、Worker reverification 2；官方 psmux 外层 Job `wc_job_MXZLlFt1DTtNwsTr`、内层 Job `3207ad49d1f24d5882e6c35332740936` 均 exit 0，15.441 秒。FFmpeg `moov atom not found` 来自损坏 MP4 负向夹具，测试通过。
- 全仓 Python 3.11 `compileall -q src/muse tests scripts` PASS。最新官方 `scripts/muse_dev.py → psmux` 全量 `unittest discover`：**2,322 tests / 0 failures / 0 errors / 1 skipped**，217.952 秒；外层 Job `wc_job_d1zFqRI7IdEAXB0S`、内层 psmux Job `d197fba246d84d69b68402254ceecc94` 均 exit 0。日志中的 FFmpeg、缺失图片、mock provider 与 CLI 参数错误均为既有负向测试的预期输出，不构成失败。
- 生产端仍未闭环：`AutonomousRuntime` 的 provider/factory 成对注入 seam 已存在，但 CLI `main()` 与 `DaemonControl.run_once()` 尚未注入真实 Provider/factory；生产 resource gate 必须使用真实 GPU VRAM、独占 lease/supervisor 状态，且 Worker/Builder 还未在 Wan dispatch 生命周期持有 `GpuLeaseSession`。全仓没有可信 GPU 电耗/电价估算；不能把本地 GPU 当作零成本，cost gate 缺少明确预算证据时必须拒绝。未执行 GPU 渲染、未自动发布。