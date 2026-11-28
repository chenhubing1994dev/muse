
## 2026-11-27 全自主模式
- D-001 旧用户记忆无认知标签 → 一律 `legacy_unverified`（仅新增字段，不改原文，先备份）。尚未执行（T7 依赖可运行的诊断）。
- D-002 Super Agent Party / dsh 无地址 → 休眠适配器（not_configured），契约测试 ≠ 集成测试。尚未执行（T8）。
- D-003 本地模型最小推理 → 按 T9 带显存保护自动执行。尚未执行。
- D-004 无法自行执行命令时：不以“看起来没问题”宣布通过；不把未运行的改动标为通过；不绕开宿主的“禁止打开可执行文件”限制（未尝试用其它扩展名或现有后台服务间接执行）。改为提供一次性人工启动的诊断监听器，由用户决定是否启用。
- D-005 T2–T10 在无法运行诊断的情况下不推进：用户的工作循环要求每个任务实测闭环，堆积未验证改动违背“只信磁盘结果”的原则，也与“无法执行命令则汇报并结束”的例外条款一致。

## 2026-11-27 全自主模式（续，监听器启用后）
- D-006 诊断只经监听器触发并以 `reports/diag_latest.json` 为准；单探针 120s 超时判 FAIL；未修改 diag_watcher.py / start_diag_watcher.bat。
- D-007 T2 视频/产品类灵感进化判为中风险（占 GPU 或产生产品）→ `pending_approval`；纯研究进化低风险自动执行。批准接口对 `route.*` 票据在批准后立即在服务内执行。
- D-008 T2 进化接口在审批前返回 `task_id: null, status: pending_approval`（旧接口会立即入队）；同一 request_id 不同参数仍返回 400。
- D-009 MuseCommander 结果落盘 `data/commander/results/<task_id>.json`（TaskQueue 不保存结果）；Workflow A/C 改用 `drain_plan`，只执行本计划票据，避免误跑其他计划的票据。
- D-010 T3 机会评分采用保守离线假设（标为 assumption/hypothesis，非预测）；成本只统计 Commander 成本账本中本计划的真实记账行；无结算回执 → 收益 `unknown`，收益账本不写任何条目。
- D-011 T3/T4 “人工发布确认”即使被 `user:` 批准，Muse 也只把交付包标为 `approved_for_manual_publish`，`publish_allowed` 始终为 false，不对外发送。
- D-012 T4 钩子在视频 Attempt 提交成功之后调用，失败只记错误账本、不回滚已完成的视频；通过 package_sha256 派生计划 ID 保证幂等。
- D-013 T5 标签位于响应顶层（`epistemic_tag/epistemic_label_zh/epistemic_scope=response`），未命中路由默认 `observation`；只覆盖 status_api 对外 API（local_dashboard 的 `/__muse__/health` 等内部端点不在范围内）。
- D-014 T6 `muse-ruflo call` 命令行调用执行型工具现在被拒（无票据），只读命令不变；`initialize_swarm(approved=True)` 作为低风险 Commander 票据执行（调用方已提供显式批准标志）。
- D-015 T7 迁移只覆盖 User Track（含软删除行）；标签行写在同库 `memory_epistemics` 侧表，`memories` 不改一字；真实库已执行，migration_id 3a24e867702448fa9c8244f6f6077bcb。
- D-016 T8 dsh 端点取自仓库内插件 README（127.0.0.1:8326 / :3099），默认 `enabled:false`；SAP 无地址 → 休眠；所有外部调用为高风险票据。L4.11 为契约测试（本地假服务端），不代表已与真实服务集成。
- D-017 T9 仅在 nvidia-smi 可用、空闲显存 ≥1024MiB、LM Studio 报告已有 loaded 模型时推理；否则 SKIP。拒绝触发 JIT 加载。

## 持续开发模式（2026-11-27 23:3x 起）
- D-018 P0-1：本机未安装 pytest 且禁止安装 → 单测阶段使用项目现有的 unittest 套件（固定 argv `[sys.executable,"-m","unittest","-v",<tests/test_*.py 模块名>]`，无 shell），结果仍写 `reports/pytest_latest.json`（字段 runner 注明 unittest）。12 批 3 并行，单批 600s，阶段预算 1200s（监听器在 1500s 强制结束 diag_auto，故未用 30 分钟）。
- D-019 真实数据守卫：测试前后比对真实记忆库逻辑指纹（memories/revisions/candidates/epistemics/migrations）+ commander/workflow/prototypes 目录清单；记忆库变化 → 立即在线恢复并保留污染副本作证据；其他 sqlite 文件变化仅作信息记录（后台 Muse 运行时可能合法写入 tasks/improvements，无法归因，不覆盖以免丢数据）。
- D-020 旧契约→新契约（tests/test_ruflo_adapter.py）：执行型 Ruflo 工具调用由“approved=True 即可执行”改为“还必须处于运行中且持租约的 Commander 票据作用域”（T6）；补断言：无票据时 `BypassCommanderError` 且子进程未被调用。
- D-021 旧契约→新契约（tests/test_inspiration_evolution.py）：带视频/产品的灵感进化由“立即 queued”改为“`pending_approval` + task_id=null，user: 批准后才进入运行时队列”（T2）；补断言：批准前运行时队列无该 request_id，`agent:` 批准 403，`user:` 批准后执行成功。
- D-022 P0-2 git 检查点不启用：本分支有每日 03:00 自动同步任务会提交/推送工作区，自动 commit 可能被其推送，违背“不 push”精神；继续使用 backups/<时间戳>/。

- D-023 C3 入口审计：人类在本机输入的 CLI 属“用户直接动作”（user_cli，仅登记以保证完整性）；自动化入口（HTTP 写路由、例程、Worker、钩子）必须为 commander_ticket / approval_gated / safety_stop / kill_switch_gated / legacy_ticket，否则如实记为 debt，DEBT_CEILING 只减不增（C3=14 块 → C4=13 → C5=12）。供复核：是否要求 CLI 也全部走票据。
- D-024 C4 守护进程 stop/pause 不走票据：停止类动作只降低活动，必须在 Kill Switch 生效时仍可用（走票据会被全局门挡住）；改为直通 + 审计 `safety_actions.jsonl`。start/resume/once 每次一张低风险票据（同一点击不去重）。
- D-025 C5 统一停止开关：Omega 面板的 SafetyController 与 Commander 原本互不影响；现规定任一开关处于 killed/paused 即关闭全局门（fail closed）。Omega 的 reset 只复位它自己的状态，不会替 Commander 解锁。
- D-026 C5 旧契约→新契约：`/api/v1/omega/evolution/rollback` 由“确认短语后立即回滚（actor=dashboard）”改为“202 + 高风险 Commander 票据，user:/human: 批准后执行，actor=批准人”。test_omega_security_contract 中仅断言错误短语 400，未受影响。
- D-027 C5 `/api/v1/radar/scan` 经票据执行；缺任务库时仍返回 400（保持原契约）。
- D-028 C6 红线白名单：pip install 仅允许两个人工双击的打包脚本（scripts/build_sidecar.py、scripts/autopilot_stage2_client_and_github.py，运行时不导入）；src 中带外网 URL 的网络模块 7 个（研究/接入/可选提供方，均有既有守卫）；User Track 直写仅 dual_track_memory；候选晋升 5 处均为人工或 Agent Track。该审计为静态警报线，不是沙箱。
- D-029 C11 取代 D-019 的“其他 sqlite 仅信息记录”：data/ 下除 handoff/（冻结副本）与 memory/（另有硬守卫）外的全部 SQLite，单测前后按表做行级逻辑指纹（含 schema）。豁免仅限实证观察到的半小时自检在快照之后追加的行（tasks: watchdog_checks、category=system 且 subject_id∈{watchdog,capability-radar} 的 progress_events；improvements: capability_radar_snapshots），历史行仍计入指纹，改写/删除/回填旧时间戳/NULL 时间戳均报警。任何其他变化 → 单测阶段 FAIL（fail closed），并把变化的库从运行前备份恢复（运行后状态复制为证据，不删除）。代价：若用户在诊断期间真实使用 Muse 写库，会出现假红并被回滚到运行前状态（证据保留可人工合并）；选择保守方案。
- D-030 C12 旧记忆复核只接受 `user:<名字>`（比统帅审批更严：`human:` 也拒绝，按任务书“只接受 user: 身份”）；只提供 confirm/revert，不提供“驳回”（驳回=改写或删除原文，属用户自行操作，避免代理触碰原文）；复核账本独立表 memory_legacy_reviews，不混入 memory_migrations（防止迁移回滚误处理复核行）。
- D-031 C14 旧契约→新契约（tests/test_memory_candidates.py）：`MemoryStore.approve_candidate` 对 track=user 的候选由“任意非空审批人”改为“必须 user:/human: 身份”（agent:/无前缀 → PermissionError，候选保持 pending，memories/memory_revisions 不写）。Agent Track 候选不变（仍由 memory_lifecycle/skillify 各自的门控）。控制台记忆审批人改为 `user:local-dashboard`（本机单用户会话 + 人工输入 APPROVE-MEMORY），任务队列 actor 仍为 local-dashboard-user 不变。补断言：5 种非人类身份被拒且库无写入、human: 放行、CLI `--by agent:muse` 被拒。命令行用户需写 `--by user:<名字>`。
- D-032 C16 研究证据计数收紧：supports 只认 add_evidence 产生且来源已读取的证据对象（其他节点的 supports 边一律忽略）；refutes 额外保留自身失败节点（只会阻止 supported、不会制造 supported，保守）。transition_claim 直接调用时，实验证据也须满足实验桥同样的条件（已完成、捕获原始输出哈希、结果匹配：supported←succeeded，refuted←failed/budget_exceeded，contested←任一）。行为变化只影响此前可被伪造的路径，现有用例与 L5.R/L5.A2 契约不变。

## D-033 `muse-memory approve` 拒绝改为退出码而非异常（2026-11-28 C21）
- 背景：D-031 后 agent:/无前缀审批 User Track 候选会抛 PermissionError，CLI 打印 Traceback。
- 决定：CLI 捕获 PermissionError → 退出码 3，ValueError（不存在/非 pending/技能未评估）→ 退出码 2；错误以 JSON 写 stderr；底层 MemoryStore 仍抛异常（L3.3 不变）。拒绝时零写入不变。
- 测试契约：test_memory_candidates 旧断言“CLI 抛 PermissionError”改为“退出码 3 + stderr JSON + 候选仍 pending + 不可检索”（更严格，非放宽）。
- 可逆：恢复 backups/cycle-C21-*/src/muse/memory_cli.py 即回到抛异常行为。
