# B373 离线灵感产品化适配层

日期：2026-11-23

## 目标

补上离线灵感演化与既有离线产品工厂之间的断点。复用 InitiativeStore 与 LocalProductBuilder，不另建产品引擎或调度器。

## 新增文件

- src/muse/inspiration_productization.py
- tests/test_inspiration_productization.py

## API

build_offline_inspiration_product(*, workspace, inspiration_id, title, summary, blueprint_path, report, hypotheses=(), uncertainties=None, counter_evidence=None, experiments=None, evolution_cycle_id=None, symbolic_framework_id=None, symbolic_framework_ids=None, allow_external_research=False)
约束：

- 只接受 report.synthesis_status == "local_offline_only"，且 allow_external_research is False。
- 只引用工作区内已存在的蓝图文件；拒绝绝对路径、路径穿越和符号链接。
- Initiative 标题由 inspiration ID 的 SHA-256 前缀确定；同一灵感重复调用可复用原 Initiative，内容不一致时拒绝覆盖。
- Initiative 以 autonomy_level="draft" 创建；调用既有 LocalProductBuilder.build() 与 verify()，产物必须保持 testing，通过现有模板、CSP、文件哈希和 ZIP 完整性校验。
- 保存假设、反证、不确定项、实验、蓝图路径与象数来源 metadata。已知体系为 iching/qimen/ziwei/liu_ren；未知体系保留原始值并标记 unrecognized_source_metadata。
- 支持单个或最多四个象数来源 ID；可完整保留易经、奇门、紫微、大六壬四种启发来源，重复 ID 拒绝，未知来源仍标记 unrecognized_source_metadata。
- 玄学内容标记为假设而非证据；不联网、不执行任意代码、不派发 Commander 任务、不启动视频/GPU、不发布产品、不自动修改 Muse 源码。

## 所有权与集成

### B381 主流程接通
- `evolve_inspiration_closed_loop(..., create_offline_product=False)` 提供显式、严格布尔 opt-in；默认关闭。
- 仅当本轮研究报告为 `local_offline_only` 且外部研究未启用时调用离线产品化适配器。
- 将象数候选的假设种子、来源 ID、报告中的反证/不确定性/实验、蓝图路径和灵感 ID 一并写入产品原型溯源；象数始终是非证据假设。
- Status API → Commander TaskQueue → AutonomousSupervisor Worker → 演化函数传递 `create_offline_product` 并纳入幂等请求比较；历史任务载荷缺少该字段时按 false 兼容。
- 产品只进入 `testing`，`draft` 自主级别；不发布、不派发、不启动 GPU、不执行生成代码、不自动改源码。

### B382 Web Portal 前端/转发接通
### B383 视频 Worker 安全接入复核（专项回归已通过，真实 GPU 待验收）
- 已存在 `VideoAdmissionRuntime` 组合根：只绑定现有 CapabilityRegistry、AgentRoleRegistry、TaskQueue、GPU 资源租约与 ComfyUI/Wan handler；不自动注册能力或角色权限。
- `AutonomousSupervisor._video_worker` 在 admission provider/request factory 缺失或 readiness blocked 时不领取任务；CLI `run/once` 与 `MuseDaemonController.run_once` 均在 `MUSE_ENABLE_VIDEO_WORKER=1` 时注入 runtime，并在 finally 关闭 registry 连接。
- Admission 要求 CommanderPlanner 持久 DAG、精确人工审批事件、受限角色/能力、成本配置、有效 GPU 租约/心跳、模型监督器协调及 ComfyUI 队列空闲；任一证据不足均 fail closed。
- Python 3.11 专项：`tests/test_video_admission_runtime.py` **13/13 passed**；`tests/test_video_task_worker.py` **38/38 passed**。这验证了模拟/隔离条件下的安全门和 Worker 契约，不代表真实 ComfyUI/Wan 渲染或 RX GPU 已验收。
- Web Portal evolve 路由允许且只允许可选严格布尔 `create_offline_product`；旧请求默认补 false，非法类型 fail closed。
- UI 分别提供普通演化与“演化并创建离线产品原型”两个明确动作；外部研究条目不展示离线产品化按钮。
- 产品化请求保留同一 CSRF/session/Host/Origin 保护，并透传给 Status API；页面明确提示 testing 与人工验收边界。

- B373 初始提交只新增适配器、测试与进度文档；B381 由演化/Commander 单写者完成 Status API → TaskQueue → Worker 正式接线，B382 再补齐 Portal 转发/UI；产品 Initiative ID 回写 ResearchKnowledgeStore metadata。
- InitiativeStore 与 LocalProductBuilder 均复用现有权威实现，没有第二套状态库或构建器。

## 验证状态

- B381 Python 3.11 定向回归：产品化、灵感演化、Autonomous Runtime **54 tests / 0 failures / 0 errors**（13.102s）；Status API→Commander→Worker→离线构建器端到端测试 **1/1 PASS**。注意：该结果不覆盖 Web Portal 反向代理层。
- B381 全仓 `scripts/validate_full_suite.py`：**2,457 tests / 0 failures / 0 errors / 1 skipped**（364.699s），Job `wc_job_Yeh3NdglTbqHmRmA` exit 0。
- B382 Web Portal 专项 `tests.test_web_portal`：**18 tests / OK**（94.136s），最终 Job `wc_job_pl4fA2ZCjbRX8TPz` exit 0；同时 `node --check web-portal/app.js` exit 0；覆盖普通演化默认 false、显式产品化 true 透传、字符串/整数/null 等非法开关拒绝。
- B382 全仓回归：**2,458 tests，1 failure**；唯一失败为 `test_running_plugin_ticket_rejects_lease_extension_without_matching_heartbeat_audit`（`tests/test_plugin_platform.py:575`），与本次 Portal 文件无关，属于并行 B381 TaskQueue heartbeat 审计修复范围。需待该 owner 完成后重新跑全仓，不将本轮标记为全绿。
- 验收范围为本地离线产品构建与软件回归；没有进行公网部署、真实 GPU 渲染、移动设备验收或产品发布。

## B384｜视频 API 入口收敛到 Commander Admission
- `POST /api/v1/video-products` 的旧 `prompt_to_video` 直通线程现返回 409 `video.commander_admission_required`，不直接启动 Wan；旧线程执行器也拒绝该模式。`local_assets` FFmpeg 素材编排保持独立可用。
- Video Product API **5/5**、Status API **49/49**、Video Admission Runtime **13/13**、VideoTaskWorker **38/38**，合计 **105/105**；最终全仓 **2,475 tests / 0 failures / 0 errors**（415.165s）。
- AI 视频只允许 Creator Initiative → Commander 人工审批 → VideoTaskWorker → Video Admission → GPU lease/成本门禁的路径；没有启动真实 runtime 或 GPU 渲染。

## B385｜测试隔离风险补记
- 首次全仓测试在项目根目录运行，10 个 AutonomousResearch 测试漏传 workspace，实际向正式 InitiativeStore / PhilosophyStore / WebsiteCatalog 写入测试产物（22 条 initiative knowledge links、66 experiences、204 philosophy links、2 pending proposals、42 website events）。没有创建视频 Attempt；没有删除或回滚数据库记录。
- 已修复 10 个测试，全部显式传入 `workspace=root`；随后从独立 cwd `.muse-test-sandbox` 重新跑全仓，**2,475 tests / 0 failures / 0 errors**（373.965s，Job `wc_job_MfAQXzRsNB42SKKu` exit 0）。测试后项目根目录三份 DB 的 mtime 未变化，新增测试数据只留在 sandbox。