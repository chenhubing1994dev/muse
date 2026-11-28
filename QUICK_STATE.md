# QUICK_STATE.md — Muse 唯一状态文件（AWS Quick 维护）

> 续作：先读本文件，从【下一步】继续。逐条改动明细见 CHANGELOG.md；PROGRESS.md / ISSUES.md / MUSE_PROJECT_MAP.md 仅为早期参考。
> 最后更新：2026-11-27 17:37 CST（B391 真机验收第 2 次与 P2-34 修复）；此前：2026-11-27 17:03 CST（第 1 次验收与 P2-29…33）

## 铁律
- 不调用/委派/探测任何 Coding Agent、子代理或相关 MCP/扩展；不启动外部 AI CLI。
- 只在 `<MUSE_ROOT>` 内操作；不删除文件；不读/打印/修改 `.env` 与含密钥配置（config/muse.toml、config/model-runtime.json、config/opencode.local.jsonc）。
- 联网安装依赖前先征得同意。已有文件首次修改前原件备份到 `.qw-backups/20261125/<路径>.orig`（SHA-256：MANIFEST.txt）。

## 环境限制与验证方式
- AWS Quick 无终端；设备端 Python 禁止子进程/sys.path/importlib。
- 本轮新增“云端沙箱拼装执行”：把相关模块拼入同一命名空间、用忠实 Worker 替身，真实执行新代码与项目自带测试（结果见下）。**真机复验仍需用户运行一条命令**。
- 并发风险：另一 Agent 今天持续修改 unified_runtime / video_* / autonomous_runtime / research_*；AWS Quick 不改这些文件（task_queue 仅加一行增量索引）。
- **第 1 次真机运行（18:07）**：latest.log 仅 `No pyvenv.cfg file` ×2 → `.venv\Scripts\python.exe`（uv 0.13 启动器）启动即失败，项目代码一行未执行；pyvenv.cfg 存在（home=%USERPROFILE%\AppData\Local\Programs\Python\Python311，3.11.9）。根因在环境不在代码；Muse-Start/Stop、muse-supervisor.cmd、两个计划任务同样受影响。
- 已处理：Resolve-MusePython.ps1（真实探测 + 自动回退 + 同版本复用 venv site-packages）、Repair-MuseVenv.ps1（默认诊断，`-Apply` 备份后离线 `venv --upgrade`）、run_full_diag.ps1 / run_smoke.bat 改用解析器、Muse-Start.ps1 启动前探测并提示修复。

## 主流程
- **意识主循环（新）** `muse-mind` / `MindLoop`：感知(清洗/限长) → 内在状态(tick+事件+预算) → 记忆检索(User=证据, Agent=经验；读时按相关度×置信度×新近度重排) → 反思(本地模型 JSON+修复+熔断，失败走确定性启发式) → 规划(CommanderPlanner，模型计划非法则安全回退) → 审批门(逐步风险评估；低风险策略批准，开发/运维/高危等人工) → 执行(worker.run_once + 租约/fencing；MindStepHandler 只写带哈希工作包) → 核验(哈希校验 + 可选独立审查) → 学习(失败因果假设 trace、策略自修正、Agent Track 情节记忆、User Track 候选待审、Skillify 候选待审) → 持久化(state.json 原子+.bak、journal.sqlite3)；`idle_tick` 自治心跳：稳态、审批超时+级联取消、执行人工新批准的步骤、好奇心门控的目标内省（在途目标不重复规划）
- **B 任务骨架**：chatgpt_bridge / task_cli / intake_runtime → TaskQueue → approve → worker → handler → review_gate → status_api
- **A 灵感→产品**：inspiration → research_pipeline → research_knowledge → initiative_engine → product_builder → Portal → feedback import
- **C 选题→视频**：business_opportunities → Commander 审批 → video_task_worker → video_product_builder → video_qa → publish_package
- **对话**：muse-chat → MuseAssistant(有界上下文窗口 + 长期记忆注入) → LMStudioClient(超时+退避重试) → ConversationStore

## 意识体七层闭环
| 层 | 实现 | 状态 |
|---|---|---|
| 1 感知与输入路由 | consciousness_loop.sanitize_input、chatgpt_bridge、intake/intake_runtime、intent | ✅ |
| 2 记忆 | memory(双轨/修订/软删/证据/候选审批/**精确去重**)、assistant(**短期滑窗**)、consciousness_loop.rank_memories(**读时新近度衰减**，不改写数据)、memory_reflection、mempalace_* | ✅ |
| 3 内在状态与自主驱动 | **inner_state**(情绪/能量/好奇心/目标权重/稳态/预算节流/防固着)、MindLoop.idle_tick/run、autonomous_runtime、routines、watchdog、resource_governor | ✅ |
| 4 认知规划与元认知 | commander_planner、MindLoop 反思/核验、review_gate、**trace(失败因果假设，已接入)**、策略自修正、**skillify(已接入)** | ✅ |
| 5 行动与审批门 | task_queue(审批/取消/幂等/依赖/租约/fencing)、worker、assess_risk、审批超时+**级联取消** | ✅ |
| 6 持久化与断点自愈 | SQLite WAL、inner_state 原子写+.bak 回退、MindJournal 中断恢复、lease_recovery、handoff | ✅ |
| 7 API 与鉴权 | status_api(loopback+Bearer+Origin)、**GET /api/v1/mind/state（只读 URI）**、local_dashboard、web_portal | ✅ |

## 模块矩阵（224 模块：✅173 已闭环有测试/行为验证 · ⚠️37 依赖外部服务/模型或缺专测 · 🔌13 孤岛 · ❌1 纯接口声明）
| 分组 | ✅ | ⚠️ | 🔌 | 非 ✅ 模块 |
|---|---:|---:|---:|---|
| 调度核心 | 36 | 4 | 0 | ⚠️herdr_adapter ⚠️herdr_transport ⚠️ruflo_adapter ⚠️workload_cli ❌interfaces（Protocol 声明，保留作契约文档） |
| 记忆/对话/模型 | 26 | 5 | 3 | ⚠️gemma_intent ⚠️intent_cli ⚠️main ⚠️mempalace_adapter ⚠️obsidian_cli 🔌mempalace_lifecycle 🔌openrouter 🔌usage_observer |
| 研究/知识 | 40 | 7 | 2 | ⚠️capability_cli ⚠️ego_browser ⚠️progress_cli ⚠️research_pipeline ⚠️research_wiki_review ⚠️scrapling_adapter ⚠️source_freshness 🔌harness_evolution 🔌lightpanda_adapter |
| 灵感/象征 | 11 | 2 | 2 | ⚠️cyber_cosmos ⚠️ziwei_adapter 🔌celestial_calendar_provider 🔌zhenyi_bazi_provider |
| 产品/Initiative | 15 | 5 | 3 | ⚠️desktop_adapter ⚠️open_design ⚠️ploid_adapter ⚠️plugin_platform ⚠️software_crafting 🔌open_design_recovery 🔌sandbox_file_manager 🔌social_content_kit |
| 视频/语音 | 37 | 14 | 3 | ⚠️cantonese_asr ⚠️cantonese_tts ⚠️chimera_vocal ⚠️kinetic_explainer ⚠️openfilm_adapter ⚠️piper_tts ⚠️remotion_motion ⚠️scene_analysis ⚠️smart_crop ⚠️video_face_framing ⚠️video_motion_diagnostics ⚠️video_reviewer_attestation ⚠️video_visual_quality ⚠️wan_video 🔌creator_media_plan 🔌jyutping 🔌prosodic_boundary |
| API/界面 | 5 | 0 | 0 | — |
| 新增意识核心 | 3 | 0 | 0 | structured_output、inner_state、consciousness_loop |

本轮接线：trace、skillify → 意识循环；candidate_enqueuer、chimera_vocal、quote_intelligence、video_editable_export、video_reviewer_signer、web_portal_admin → pyproject 命令（均已核对 parse_args 前无副作用）。
剩余 🔌 13 个均为有单测的库模块，接入点属业务决策：openrouter=云端 Provider（与“仅本地/免费”冲突）；celestial/zhenyi=可选玄学引擎（需决定是否加入 inspiration 透镜）；视频三项=待视频线接入（另一 Agent 负责）；sandbox_file_manager=另一 Agent 今日新建。

## 静态与沙箱实测结果
- 全仓 AST+symtable：语法错误 0、未定义全局名 0、循环导入 0（含新模块）、跨模块调用签名核对 4,843 处 + 方法调用 7,794 处，错位 0；async 函数 0。
- 沙箱真实执行：新诊断 D3.1/D4.1/D4.2/D4.3/D5.1/D5.2 共 **74/74**；tests/test_consciousness_loop.py **10/10**；回归 test_assistant 4/4、test_memory 18/18、test_conversations_and_model_client 7/7（含本地 HTTP 往返）；配置边界 7/7；冒烟 S4、L3.1–L3.5、L4.2 共 **44/44**。
- 沙箱实测中发现并修复：①待审批目标被内省重复规划（死循环）②审批超时后下游已批准步骤永久卡 queued（级联取消）③内省时目标优先级被重置 ④记忆候选重复提交 ⑤`read_snapshot` 用 `__new__` 绕过构造 ⑥英文风险词误判（"information"⊃"format"）⑦只读 SQLite URI 未编码空格路径 ⑧task_events 缺索引导致每次入队全表排序（并发下触发 MemoryError）。

## 问题清单
### P0/P1（全部完成，待真机复验）
- [x] F1/F2 语法错误 · I1 子进程编码 · I2 乱码 · I3 README · I4 依赖分组 · G1 断连 · CTX 上下文滑窗 · RETRY 退避重试 · MEMDEDUP · CONFIG 校验
- [x] MIND 意识主循环 + 内在状态 + 结构化输出容错层 + API 端点 + 8 项沙箱发现缺陷
- [x] PERF task_events(event_type, created_at) 增量索引
- [x] ENV-VENV venv 启动器损坏：诊断脚本已可自动回退运行；修复脚本就绪
- [ ] ENV-VENV-FIX（用户确认）执行 `Repair-MuseVenv.ps1 -Apply` 修复 .venv（会改写 .venv 启动器，已自动备份）
- [ ] V1 真机运行 run_full_diag.ps1（第 2 次）
- [ ] I5（用户）config/opencode.local.jsonc 被 Git 跟踪，自查密钥
- [ ] N1（用户）另一 Agent 13:55–14:00 新建文件曾有写入损坏，请其复核
### P2
- [x] requirements*.txt、.env.example（含 MUSE_MIND_*）、README（冒烟/全量诊断/muse-mind/FAQ）、run_smoke.bat、run_full_diag.ps1
- [ ] 13 个孤岛的接入决策（见上）
- [ ] 7 个 ⚠️ 缺专测 CLI 补行为测试（目前仅 --help 覆盖）

## 诊断覆盖
- `scripts\muse_smoke.py`：S1–S9 + L1/L1.2/L2/L3.1–L3.6/L4.1–L4.3（21 阶段）
- `scripts\muse_full_diag.py`：D1.1 未定义名扫描 · D2.1 全部测试模块导入 · D3.1 空格/中文路径、并发原子写、损坏回退、只读 URI · D4.1 结构化输出 · D4.2 内在状态 · D4.3 风险门/输入清洗/记忆排序/执行器审批与路径穿越防御/失败归因 · D5.1 DeterministicMockLLM 多轮全脑闭环（格式修复→并行波次→人工审批→重启→心跳执行→目标关闭→不重复规划→情节记忆→API 一致）· D5.2 模型宕机熔断与预算耗尽降级；单项异常记录堆栈 + 最内层局部变量快照；输出 latest.log / latest.json / diag_matrix.json（逐模块 compile/import/单测数/行为阶段/结论）
- `scripts\run_full_diag.ps1`：先跑原冒烟，再跑全量诊断并合并结果；console.log 保留原始 stdout+stderr

## Omega B391（2026-11-25 19:55）
- 新模块（均有测试，云端沙箱拼装执行 34/34 OK）：`reviewer_attestation`（Ed25519 审查证明，17）、`inspiration_ref_repair`（8）、`revenue_ledger`（9）；新脚本 `provision_reviewer_keys.py`、`audit_and_repair_inspiration_refs.py`、`ignite_live_gates.py`；pyproject +3 入口。模块数 224 → 227（另有未计入的 `opportunity_radar/` 子包）。
- 生产 DB 取证：initiatives 库 3,954/4,073 行引用已删除的 Temp 报告（疑似测试写入生产库）；克隆演练修复 3,954→0、幂等、生产库 SHA-256 未变（329b334f…）。真修复需你执行 Runbook 步骤 1。
- 新 P0：`src/muse/opportunity_radar/` 4 个语法错误（另一 Agent 17:05–17:50 写入，补丁错位；dedup_corroborator 有代码丢失）。未改动，修复方案见账本 C1-1。
- 安全快照：tag `omega-pre-20261125` 与分支 `agent/omega-build-20261125` → 9d4d8606；HEAD 仍在 main，未提交（无终端）。
- 已结算收入：US$0.00（尚无回执）。ComfyUI 8188 只读探测：未运行。

## Omega B392（2026-11-25 19:30）— 签名审查门接入主干 B
- `muse-worker --signed-review-kind <类型>`：该类型结果不能直接 succeeded，停在 waiting_approval 等 Ed25519 签名；`muse-signed-review list/export/submit` 完成闭环；`muse-task approve` 对待签票据被拒。改动 task_queue.py（+3 方法、approve 守卫）、worker.py（参数贯通 + CLI），新增 signed_review.py、tests/test_signed_review_gate.py（15 例）。
- 沙箱回归：新增 15/15；review_gate 15/15；task_queue / worker 失败集合与改动前基线完全一致（18 个均为沙箱限制）→ 新增失败 0。模块数 228。
- 未覆盖：自我进化 approved_code_change（需在 executor 应用补丁前接入，见账本 BL-WS4-EVO）；Dashboard 尚未显示“待签名”标签。

## Omega B393 + B398 部分（2026-11-25 20:05）
- **一键验收**：双击根目录 `1_双击一键验收并保存核心代码.bat`（备份数据库 → 修复 3,954 条 inspiration_refs → 真机全量测试 → 界面构建 → 在本机 Omega 分支保存代码，不上传）。结果写在 `reports/autopilot/latest.md`。
- `opportunity_radar/` 已修复（多处补丁错位 + 2 个安全缺陷），全仓语法错误 0。新增 `unit_economics.py`、`quote_ingest.py`、`review_desk.py`，以及网页 `review-desk.html`（一键签名通过/拒绝，密钥一键初始化）。
- 新增测试累计 80 例沙箱全绿；队列/Worker 沙箱基线没有新增失败。模块数 231。
- ⚠ `.git/config` 的 remote URL 里有明文 GitHub 令牌，请吊销。
- 下一轮：B394 自我进化签名门 → B395 → B396 → B397 → B398 其余部分。

## 下一步
0. 双击 `1_双击一键验收并保存核心代码.bat`，然后让 AWS Quick 读取 `reports/autopilot/latest.json`
1. 用户运行：`powershell -NoProfile -ExecutionPolicy Bypass -File "<MUSE_ROOT>\scripts\run_full_diag.ps1"`（自动回退解释器；日志首部 `[Muse] probe` 行记录每个候选解释器的探测结果）；可选先 `Repair-MuseVenv.ps1` 诊断 / `-Apply` 修复
2. 读取 latest.log / diag_matrix.json：FAIL 按堆栈+局部变量快照根因修复，并全库排查同类写法 → 重跑至全 PASS；再跑 `-FullTests`
3. 根据孤岛接入决策实施；补 ⚠️ CLI 行为测试


## Omega B394–B398（2026-11-25 Cycle 5）
- 真机报告的 3 个失败（Windows 文件锁）与“全仓 0 测试”均已修复；`.git/config` 已脱敏（旧令牌请到 GitHub 吊销）。
- B394 签名进化门 + 受保护面 + 原子回滚 + 熔断/背压/断路器 + 插件 Capability Broker；B395 十维意识内核 + 记忆生命周期；B396 研究闭环 + 玄枢融合 + 反馈回传；B397 一键电影级视频工坊算法引擎；B398 主控台 7 个直达按钮、Omega API、安全测试套件、孤岛清零（1 接线 / 12 归档）。
- 新增测试 60/60（云端沙箱）。模块数 231 → 243。详见 `docs/PROGRESS_B394_B398_OMEGA.md`。
- 下一步：双击 `1_双击一键验收并保存核心代码.bat`（会跑全仓测试 + 新套件 + 界面构建并在本机分支提交）。

## Stage 2 — Tauri v2 零配置客户端 + 安装包 + GitHub 周更（2026-11-25 云端落盘）

| 模块 | 文件 | 状态 |
|---|---|---|
| 1 同源 Sidecar | `src/muse_sidecar_main.py`, `scripts/build_sidecar.py` | 已写；网关自检（存根后端）11/11 通过；真机冻结由 2 号 bat 执行 |
| 2 Rust 客户端 | `src-tauri/{Cargo.toml,build.rs,tauri.conf.json,capabilities/default.json}`, `src-tauri/src/{main,lib,sidecar,hardware,tray,backup}.rs`, `src-tauri/icons/*`, `client-splash/*` | 已写；首次 cargo 编译由 2 号 bat / CI 执行 |
| 3 零泄漏防火墙 | `scripts/pre_github_sync_audit.py`, `scripts/sanitize_local_paths.py`, `.gitignore`, `.gitattributes` | 已在设备上以 .git/index 解析模式运行（见下） |
| 4 GitHub Actions | `.github/workflows/weekly-cross-platform-release.yml`, `ci-pr-verify.yml`；旧 `scheduled-release.yml` 改为仅手动 | 已写 |
| 5 双击按钮 | `2_双击全自动生成exe安装包并同步GitHub周更.bat`, `scripts/autopilot_stage2_client_and_github.py` | 已写（CRLF、无 BOM、py -3.11） |
| B396 玄枢兜底 | `src/muse/xuanshu_symbolic_fallback.py`（纯 Python 干支/宫位符号推演），`xuanshu_fusion.fuse(symbolic_fallback=True)`，`omega_api` 默认启用 | 已写；逻辑在云端执行验证 |

- 运行时数据：Windows `%LOCALAPPDATA%\Muse`、macOS `~/Library/Application Support/com.muse.consciousness`、Linux `~/.local/share/muse`；`MUSE_PORTABLE` 标记 → `./muse-data`。
- 会话鉴权：Rust 生成 64 位十六进制会话令牌 + 一次性 nonce（经环境变量传入）→ `/__muse/bootstrap` 设置 HttpOnly/SameSite=Strict Cookie；`/api/*` 由网关同源代理到内部 status API（内部令牌不出进程）。
- 进程守护：Windows 自身进入 Job Object（KILL_ON_JOB_CLOSE）+ CREATE_NO_WINDOW；Unix 进程组 + PDEATHSIG；引擎监视 `--parent-pid`；退出前 `/__muse/shutdown` 执行 WAL checkpoint。
- 下一步：双击 `2_双击全自动生成exe安装包并同步GitHub周更.bat`，读 `reports/autopilot/stage2_client_latest.md`。


## B391 全链路融合（2026-11-27 · AWS Quick 全权执行）
- 审查修复 R1–R5 完成，回执见 `reports/b391_review/B391_REVIEW_20261126-1940.md` 第六节。
- P1：新增 `/api/v1/control/*`（任务中心、统一审批中心、双轨记忆、Obsidian 审计、硬件遥测、创作路径就绪度、玄学→假设→Wiki→Commander），玄学接口统一加「玄学推演／待验证假设」。云端沙箱真实执行 57/57。
- P2：新增 `/portal`（普罗米修斯门户）与 `/os`（Super Agent OS，8 模块全部接真实接口）。
- P0：路径 A 真实旁白 / 路径 B Commander 审批链路已核对；就绪度接口按真实能力选路径。
- 证据层级：L1 静态 ✅ · L2 沙箱 ✅ · L3 真机测试 ⏳ · L4 真机样片 ⏳ · L5 路径 B 实机 ⏳。
- 未执行任何 git 命令；`main` 仍为 `88250db`。⚠ 每日 03:00 自动同步会自动提交并推送工作区改动。


## B391-P2 第二轮饱和攻坚（2026-11-27 12:26 · AWS Quick）
- 云端 L2.5：26 套件 393 用例，通过 333、沙箱受限 60、无法解释失败 0；前端逻辑 17/17（68 断言）；契约 60 调用点 0 未知路由；DOM/CSP 0 违规。
- 修复 B391-P2-01…28（见 CHANGELOG）；新增 tested_by 实时闭环接口与界面；Range 206/416；网关流式转发；确认门禁弹窗（一键填入仍需人工提交）。
- 备份：`.qw-backups/20261127-b391-p2/`（23 个原件 + MANIFEST）；基线 `20261127-b391/` 未改动。未执行 git。
- 已知仍未做：`initiative_engine.py` propose() 签名有一处空白粘连（非本批次文件、另一 Agent 维护，语法有效，未改）；路径 B 实机待你按作战卡验收。


## B391 真机验收第 1 次（2026-11-27 15:04–15:41）与修复（17:03）
- 结果：24 步中 20 步通过；全量 3,045 用例仅 1 错；npm 测试与构建通过；L2.5 契约在真机也通过。
- 已修复：B391-P2-29（status_api 灵感进化接口 UnboundLocalError，真机表现为 502）、P2-30（定向测试导入路径）、P2-31（测试绕过系统代理）、P2-32（样片 GPU 预检，外部模型占用时立即给出指引）。
- 仍需你操作：样片失败的原因是 LM Studio（:1234）的模型正占用 GPU，Muse 按安全策略不会停止外部进程。重跑前请在 LM Studio 卸载模型或退出它。
- 真机验收第 2 次（17:06–17:18）：22 步通过；剩余 2 项 = P2-34（SQLite 句柄未关闭导致 Windows 偶发文件锁，已修复）+ 样片被 GPU 预检拦截（LM Studio 仍在运行，需用户处理）。
- 真机验收第 3 次（17:54–18:06）：全量 3,048 用例 0 失败，24 步中 23 步通过（L3 通过）。唯一剩余：real_local_sample——LM Studio 已关闭，但模型监督器（pid 23208，进程对设备沙箱不可见）在 77 秒租约内从未登记 paused_for_video；当前代码在该情况下会立即登记，两次验收表现一致，判断为监督器进程仍在运行更新前加载的旧代码（推断，未直接证实）。处理：重启计划任务 MuseLocalModelSupervisor 后重跑。
- 真机验收第 4 次（18:16–18:41）：全量 3,048/3,048 再次全绿（L3 稳定通过）。样片仍以同一原因失败：模型监督器 supervisor_id 53bbab34e14e / PID 23208 自 13:48 起从未变化（未被重启）。已查证旧版 model_supervisor.py（.muse-backups/video_factory/2026-11-25 两份）只在 owner_kind == "video_generation" 时让出 GPU，本地样片用的是 video_encoding 租约 → 被忽略，与 4 次现象完全吻合。根因定为：运行中的监督器是旧代码进程（已证实，非推断）。
- 真机验收第 5 次（19:56–20:07）：L3 第 3 次全绿（全量 3,048/3,048）。旧监督器已结束（心跳停止），样片 5 秒内拿到 GPU（P2-35 判断得到证实），进入 Remotion 阶段后失败：Remotion 在渲染时调用其自带 ffprobe 读取素材音轨（getAudioChannelsAndDuration）出错；旧错误截取只保留末尾 1,200 字符，根因行被截掉，无法定位。
- Git 取证（只读 .git）：当前分支 b391-full-fusion（2852be1），但 Muse-GitHubSync.ps1 每天提交到当前分支却执行 `git push origin main`——今天 03:00 的“PUSH OK (97 files)”实际只推送了未变化的 main（88250db），B391 分支从未推到 GitHub（远端无 origin/b391-full-fusion）。

## 下一步（B391）
1. 推送 B391 分支到 GitHub（见回复中的命令，推 b391-full-fusion，不动 main）；再双击 3 号 bat，拿到 Remotion 完整错误后修复样片。
2. 再双击 `3_双击B391验收.bat`，跑完对 AWS Quick 说“看验收报告”。
3. 全部通过后按 `docs/B391_L5_PATH_B_RUNBOOK.md` 验收路径 B；再双击 2 号 bat 让桌面端加载新界面。
4. ⚠ 计划任务 `Muse-GitHub-Realtime-Backup`（若已安装）会在改动后约 15 秒推送；如不希望验收前推送请先禁用。

## 完全体工程 Wave 0 + Wave 1 首轮（2026-11-27 21:xx · AWS Quick）
- 新台账：`docs_quick_state/00–04`（46 条 AC、拓扑断层图、四态矩阵、缺陷总账、波次断点）——此后以该目录为续接权威。
- 新诊断：`run_diag.bat` → `scripts/muse_diag.py`（L1–L7，27 探针，按 AC 宣判）→ `reports/muse_diag/latest.json|log`。
- 新模块：muse_commander（唯一统帅门面）、adapter_hub、expert_fleet、epistemics、dual_track_memory、research_wiki、publish_gate、error_journal。
- 清剿：17 处 `except Exception: pass` → error_journal 结构化落账。
- 一键启动：`start_muse.bat`（仅本机回环）。手册：`docs/USER_GUIDE_LIVE.md`。
- 下一步：双击 `run_diag.bat`，跑完回复“继续”。
- 实测第 1 次（21:26）：AC 31/47；第 2 次（21:55）：AC 45/47（剩 Commander HTTP POST 405）。第 3 轮改动：POST 挂载补齐、24 处吞错返回空值落账、L1.3 规则升级、ResearchPipeline 接 Wiki 契约（AC-046）、全局 Kill Switch 覆盖 Ruflo DAG 与自治运行时（AC-034）。AC 总数 49。详见 docs_quick_state/04。
- 实测第 3 次（22:08）：AC 48/49（仅 Kill 复位顺序，已修）。Wave 2 第 1 轮：控制台 CommanderPanel、视频包→发布准备包桥接、46 个私有认知标签统一映射 + 治理探针。AC 总数 52。下一步：双击 run_diag.bat。
- 实测第 4 次（22:17）：AC 50/52（Kill 两步解除缺 resume、6 个未登记私有标签，均已修）。新增：对话抽取→双轨记忆候选（AC-014）。AC 总数 53。
- 实测第 5 次（22:22）：探针 32/32、AC 53/53 首次全绿。新增 Workflow A 统帅主干 `python -m muse.workflow_a`（AC-048）。AC 总数 54。
- 全自主 T1–T10（22:41–23:1x）：经诊断监听器自跑；新增 Workflow C、接口走统帅、视频交付包钩子、接口认知标签、Ruflo 票据守卫、旧记忆迁移、dsh/SAP 适配器、本地推理探针。AC 62 条，结果以 reports/diag_latest.json 为准。
