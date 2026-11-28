# Muse 实操手册（随功能实测同步更新 · LIVE）

> 状态标注只认 `reports\muse_diag\latest.json`：🟢 已实测通过 · 🟡 已接线待运行验证 · 🔴 缺失/失败 · ⚪ 远期预留。
> 本版：2026-11-27（Wave 0 + Wave 1 首轮）。当前所有新能力均为 🟡，等待第一次 `run_diag.bat`。

## 0. 三个入口
| 做什么 | 怎么做 | 说明 |
|---|---|---|
| 全面体检（L1–L7） | 双击 `run_diag.bat` | 只在 `tmp\muse_diag run … 诊断\` 临时目录读写，跑完自动清理；报告在 `reports\muse_diag\latest.log / latest.json`。加 `--keep` 保留临时目录，`--only L4,L5` 只跑指定层 |
| 一键启动控制台 | 双击 `start_muse.bat` | 仅本机 `127.0.0.1`；先打印统帅状态，再新开 “Muse Dashboard” 窗口并打开浏览器。需要已构建 `ui-prototype\dist` |
| 统帅命令行 | `python -m muse.muse_commander status|capacity|kill|reset-kill` | 在项目根目录、已设 `PYTHONPATH=src` 时运行；`kill --reason "原因"` 立即阻断派发并取消未启动票据 |

## 1. 唯一统帅 Muse Commander（Workflow B）🟡
- 代码：`src\muse\muse_commander.py`，组合已有的 TaskQueue（唯一票据库）、SafetyController（暂停/熔断/Kill Switch）、assess_risk、IndependentReviewGate、UsageLedger。
- 流程：`submit_goal(目标, 步骤DAG)` → 每步风险分级 → 中/高风险自动 `waiting_approval` → `approve(task_id, approved_by="user:你")`（`agent:`/`worker` 身份一律被拒）→ `run_next/drain` 带超时与取消信号执行 → 客观审查（复算哈希 + 测试证据 PASS/FAIL）→ 写入 `receipts.jsonl` 哈希链凭证。
- 验证：`run_diag.bat` 中 L4.1 / L4.2 / L4.3 / L5.B。
- 紧急停止：`python -m muse.muse_commander kill --reason "手动"`；或在 Commander 数据目录放置 `KILL_SWITCH` 文件。复位：`reset-kill`。
- **全局 Kill Switch（🟡）**：`kill` 会在 `data\commander\KILL_SWITCH` 写入停止文件，Ruflo 多智能体派发（`MuseRuntime.dispatch_commander_once`）与自治运行时心跳（`AutonomousSupervisor.run_tick`）入口都会检查它并拒绝执行。复位只能在本机运行 `python -m muse.muse_commander reset-kill`（HTTP 接口故意不提供复位）。
- 仍待收口：MuseRuntime 内部 Ruflo DAG 执行逻辑保留原实现（由视频线维护），目前只在入口服从统帅全局门。
- **HTTP 接口（🟡，本机回环 + Bearer 令牌，挂在 status API 内）**：
  - `GET /api/v1/commander/status | approvals | capacity | adapters | fleet`
  - `POST /api/v1/commander/approve` `{"task_id":"…","approved_by":"user:你","confirmation":"APPROVE"}`
  - `POST /api/v1/commander/cancel` `{"task_id":"…","by":"user:你"}`
  - `POST /api/v1/commander/kill` `{"actor":"user:你","reason":"…","confirmation":"KILL"}`
  - 非 `user:`/`human:` 身份批准返回 403；缺确认字样返回 400。数据目录：`data\commander\`。
  - 2026-11-27 21:26 首次实测已通过：DAG 顺序、高风险挂起、非人类批准拒绝、独立审查（AC-020/021/022/025）🟢。

## 1.1 统帅控制台面板（🟡）
- 双击 `start_muse.bat` 打开控制台，总览页第一张卡片「统帅控制台」：
  1. 「操作人身份」保持 `user:owner`（或填 `user:你的名字`）；
  2. 「待人工审批」列表逐条显示风险等级与原因，点「批准」会弹窗二次确认，点「拒绝」直接取消该票据；
  3. 「适配器四态」悬停可看探测详情；
  4. 「紧急停止 Kill Switch」需连点两次；生效后 Commander、Ruflo 多智能体派发、自治运行时全部停住。
- 复位紧急停止只能在本机：`python -m muse.muse_commander reset-kill`（依次：删除停止文件 → 复位安全状态到“暂停” → 显式恢复运行）。
- 修改界面后需重新构建：在 `ui-prototype` 目录执行 `npm run build`（诊断 L6.6 会自动执行一次本地构建，不联网安装）。

## 2. 双轨记忆（User Track / Agent Track）🟡
- 代码：`src\muse\dual_track_memory.py`（在现有 `memory.py` 之上加认知标签侧表 `memory_epistemics`）。
- 规则：用户亲口说的 → `remember_user_statement`；Muse 的经验 → `record_agent_experience`；Muse 对你的推断 → 只能 `propose_user_promotion`，必须由 `user:`/`human:` 身份 `confirm_promotion` 后才进入 User Track。
- 对话里的记忆提案：开启记忆抽取时，模型从你的话里抽到的“关于你的事实”只会成为待确认候选（标记为 Muse 推断），不会直接进入 User Track，也搜不到；你确认后才生效。抽取器出错会记入错误账本。验证：L3.5。
- 体检：`contamination_audit()` 列出 User Track 中带 Agent 来源的污染行。验证：L3.3。

## 3. Obsidian 同步 🟡
- 已批准记忆导出：`muse.obsidian_vault.export_approved_memories(store, 库路径)`；Research Wiki 导出：`export_research_wiki`。
- 验证（L3.4）：二次导出文件哈希不变；预置的手写笔记字节不变。真实库路径用环境变量 `MUSE_OBSIDIAN_VAULT` 指定。

## 4. 八类认知标签与 Research Wiki 🟢（核心规则已实测）
- 旧模块里的 46 种私有标签（如 `symbolic_hypothesis_only`、`reported`）统一由 `epistemics.canonical_tag()` 换算成八类标签；新增未登记的私有标签会让诊断 L1.5 失败。
- 标签：`src\muse\epistemics.py` —— 事实/观察/解释/经验/假设/象征灵感/传闻/虚构设定；只有“事实、观察”可作证据；象征灵感强制带「玄学推演／象征灵感／待验证假设」锁定标签。
- Research Wiki：`src\muse\research_wiki.py` —— Claim/Source/Evidence/Experiment/Decision/Failure/Alternative 七类对象；URL 先是 `pending_fetch`，读过并记录摘录与哈希后才能挂证据；同源转载合并为一个独立声音；没证据就输出 `insufficient_evidence`。验证：L5.A。
- 生产研究流水线（`ResearchPipeline`）写入的来源已带 Wiki 契约：搜索命中 `pending_fetch`，读过的页面 `read` + 摘录 + 内容哈希（只升不降）。验证：L5.R。

## 4.1 Workflow A 一条命令跑完（🟡）
- 在项目根目录（已设 `PYTHONPATH=src`）：`python -m muse.workflow_a "你的问题" [--obsidian-vault "D:\你的库"]`
- 自动完成 4 步并各自留下凭证：象征灵感→可检验假设写入 Research Wiki → 设计实验与替代解释（离线时诚实输出“证据不足”）→ 写入 Agent Track 经验（绝不写 User Track）→ 生成本地静态原型（`data\prototypes\…\index.html`）。
- 第 5 步“受控进化提案”是高风险，会停在控制台「统帅控制台 → 待人工审批」；批准后只生成提案草案 `data\workflow_a\<plan_id>\evolve.json`，不会修改任何代码。
- 验证：L5.A2。

## 5. 象征实验室（易经/紫微/奇门/六壬）🟡
- `muse.xuanshu_fusion.fuse(问题, seed=..., symbolic_fallback=True)` → `metaphysics_label.symbolic_to_hypotheses` → `write_hypotheses_to_wiki`（只生成 `proposed` 的待验证 Claim）。任何把结果标成证据/预测的改动都会被 `validate_labels` 拒绝。

## 6. 受控自我进化 🟡
- 进化补丁作为高风险步骤进入 Commander，必须人工批准；`EvolutionVersionLedger.record` 记录原件，`rollback(version)` 原子恢复。验证：L5.E。

## 7. 多媒体与发布准备包（Workflow C）🟡
- `src\muse\publish_gate.py`：`validate_artifact` 校验 WAV 头/时长、MP4 `ftyp`、PNG 签名、SRT/HTML/JSON/MD 结构；`build_publish_bundle` 生成 `publish_manifest.json` + `PUBLISH_CHECKLIST.md`，`publish_allowed` 永远为 false，只能人工发布。
- 视频成片入包：`publish_gate.bundle_from_video_package(视频包目录, 输出目录, title=…)`，校验 `video.mp4`（ftyp）、`subtitles.srt`（至少一条字幕）、可选 `cover.jpg`（JPEG 头）；缺字幕或伪造视频会被判无效。验证：L6.3。
- 收益账本：曝光（view/like…）只记信号；“结算”必须有已验证回执；净收益单独计算。验证：L5.C、L6.3（需 FFmpeg）。

## 8. 适配器与专家舰队 🟡
- `src\muse\adapter_hub.py`：Ruflo / dsh / Super Agent Party / agent-desktop / herdr / LM Studio / FFmpeg / Obsidian / MemPalace 四态健康报告；外部框架调用必须持有 Commander 为该票据签发的授权。
- `src\muse\expert_fleet.py`：15 个专家角色，含能力、工具权限白名单、风险上限、真实执行器绑定。验证：L4.6、L4.7、L6.2。

## 9. 常见问题
- `.venv` 报 `No pyvenv.cfg file`：脚本会自动回退到 `py -3.11` / `python`；修复 `.venv` 见 `scripts\Repair-MuseVenv.ps1`（需你确认后 `-Apply`）。
- 报告里 `SKIPPED_ENV`：表示本机缺该环境（如 FFmpeg、LM Studio 未运行），不算通过也不算失败。

## 10. 2026-11-27 全自主轮新增能力（均已由诊断实测，状态以 reports\diag_latest.json 为准）

### 10.1 非交互诊断与监听器 🟢
- `run_diag_auto.bat`：不暂停、不等输入；退出码 0=全部通过。结果 `reports\diag_latest.json|md`，完整输出 `reports\diag_last_run.log`。整体超时 20 分钟，单探针 120 秒（超时算失败）。
- `start_diag_watcher.bat`：保持窗口开启后，在 `reports\` 新建空文件 `diag_request.flag` 即触发一次诊断（只会运行 `scripts\diag_auto.py`）。
- 只跑部分探针：`python scripts\muse_diag.py --probe L5.A3,L5.C1`。

### 10.2 灵感/研究接口走统帅票据 🟢（L5.A3 / AC-049）
- `POST /api/v1/inspirations`、`/api/v1/inspirations/evolve`、`/api/v1/control/xuanshu/hypotheses`（写 Wiki）都会先创建 Commander 票据，在服务内最多等待 30 秒执行；响应里带 `commander.ticket_id / status / receipt_sha256`。
- 带 `enqueue_video` 或 `create_offline_product` 的进化请求是中风险，返回 `status: pending_approval`，到控制台「统帅控制台」批准后才会交给运行时队列。
- 绕过统帅直接调用核心函数会被拒绝（`BypassCommanderError`），并记入 `data\commander\bypass_audit.jsonl`。

### 10.3 Workflow C 一条命令 🟢（L5.C1 / AC-076）
- `python -m muse.workflow_c "你的问题"`（项目根目录，`PYTHONPATH=src`）。
- 自动完成：商业机会评估（必须列反证据；单位经济仅为模拟，不是预测）→ 静态原型与交付说明 → 成本核算（只统计真实记账行）→ 本地交付包 `data\workflow_c\<plan_id>\bundle\` → 收益回写（没有经验证的结算回执就记 `unknown`，只写 Agent Track）。
- “人工发布确认”是高风险步骤，停在待审批；批准后 Muse 也只把交付包标为“可人工发布”，绝不自动上传或发帖。

### 10.4 视频成片自动生成交付包 🟢（L5.C3 / AC-077）
- 视频任务提交成功后自动调用 Workflow C 的交付包/收益/发布门（同一套票据），交付包在 `data\workflow_c\<plan_id>\bundle\`。缺字幕或视频损坏会让交付步骤失败（不影响视频本身已完成的状态）。

### 10.5 接口认知标签 🟢（L6.7 / AC-084）
- 每个 JSON 响应顶层都有 `epistemic_tag`（八类之一）与 `epistemic_label_zh`；玄学接口恒为“象征灵感”。新增路由若漏标，诊断会失败。

### 10.6 Ruflo 执行收归统帅 🟢（L4.10 / AC-035）
- Ruflo 的执行型工具（swarm_init / agent_spawn / agent_execute / memory_store）只能在“运行中且持有租约”的票据里调用；紧急停止时全部拒绝。`initialize_swarm` 本身作为一张 Commander 票据执行。
- 注意：命令行 `muse-ruflo call agent_execute --approve` 现在会被拒绝（没有票据），这是有意关闭的绕过路径；只读工具（status/tools）不受影响。

### 10.7 旧记忆迁移 🟢（L3.6 / AC-015）
- 已对真实记忆库执行：3 条旧 User Track 记忆标为 `legacy_unverified`（待你复核，不是“已确认”），原文与条数未变。备份：`backups\memory\`；报告：`reports\memory_migration\`。
- 预览：`python scripts\migrate_legacy_memory.py`；执行：加 `--apply`；回滚：`python scripts\rollback_legacy_memory.py <migration_id>`（本次 ID：3a24e867702448fa9c8244f6f6077bcb）。

### 10.8 外部智能体 dsh / Super Agent Party ⚪（L4.11 / AC-036，契约测试，非集成测试）
- 代码：`src\muse\external_agents.py`；配置模板：`config\external_agents.example.json`（复制为 `external_agents.json` 并 `enabled: true` 才会启用）。
- dsh：仓库文档给出本机网关 `127.0.0.1:8326`、内核 `127.0.0.1:3099`；实测当前未运行。Super Agent Party：仓库中没有任何地址，保持休眠（not_configured）。
- 每次调用都是高风险票据，需 `user:` 批准；外发只允许 model/messages/max_tokens/temperature 字段、system/user 角色、≤8000 字符，并且只能发往本机地址。

### 10.9 本地模型最小推理 ⚪（L5.M1 / AC-093）
- 有 nvidia-smi、空闲显存 ≥1GB、LM Studio 已加载模型时自动执行一次 ≤16 token 推理并检查显存回落；否则显示 SKIP 和原因（当前：未找到 nvidia-smi）。不会加载或下载模型。

### 10.10 审计链校验 🟢（L5.H1 / AC-097）
- `python -m muse.audit verify`（默认校验本项目 `data\commander`；可加 `--root 其他目录`）：检查完成凭证链、越权/安全动作/Ruflo 审计链，以及成本账本与成本链逐行一致；退出码 0=全部完好，1=发现篡改或缺失。

### 10.11 诊断附加能力 🟢
- `run_diag_auto.bat` 现在还会运行全量单元测试（unittest，约 4–5 分钟，结果 `reports\pytest_latest.json`）、生成四态账本 `docs_quick_state\05_LEDGER_AUTO.md`、执行性能预算（前 3 次采集基线）。
- 只跑部分探针：`python scripts\muse_diag.py --probe L5.D1,L5.B2`；保留临时目录：`python scripts\muse_diag.py --keep`；只跑某几层：`python scripts\muse_diag.py --only L1,L4`。
