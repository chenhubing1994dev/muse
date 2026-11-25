# BUILD_LEDGER_OMEGA.md — Muse Omega 构建账本（只追加）

> 规则：只追加、不改写历史条目。每条记录写明命令/方法、真实输出摘要、哈希与状态等级。
> 章程：`docs/AGENT_BUILD_CHARTER.md`。状态等级：`WANTED → CODE_EXISTS → TESTS_PASS → LOCAL_SANDBOX_RAN → LIVE_HARDWARE_RAN → USER_ACCEPTED → COMMERCIALLY_VALIDATED`。

---

## §0 冲突裁决（2026-11-25 18:31 CST，Cycle 0）

| # | 冲突 | 裁决 | 依据 |
|---|---|---|---|
| R1 | 章程 §0.2 要求积极委派 ACP Coding Agent（Kiro / Claude Code / Codex…） | **不执行**；不探测、不唤醒、不委派 | 创作者 2026-11-25 既有绝对禁令（QUICK_STATE 铁律 1：额度耗尽）；按真相层级，既有明确用户指令优先 |
| R2 | 章程要求 `git checkout agent/omega-build-20261125` + 每项原子提交 | 已写入 ref 指针（见 C0-3），**HEAD 仍在 `main`，未提交** | AWS Quick 设备沙箱禁止子进程，无法运行 git；手写 index/对象风险过高。提交命令见 Runbook §0 |
| R3 | 章程要求运行全量 Python unittest / compileall / Vite / svelte-check | **未执行全量**；新代码在云端沙箱“拼装执行”真实运行 | 设备端禁 subprocess/sys.path/importlib；且 QUICK_STATE 记录 `.venv` 启动器损坏（ENV-VENV） |
| R4 | “独立 Reviewer” | 本批次只有**同一执行者的审查轮次 + Ed25519 ephemeral_test 签名演练**；明确**不构成**独立审查 | 章程 §1.3-4：actor 字符串不算审查；测试密钥永不当生产密钥（G9） |
| R5 | 另一 Agent 今日并发修改 unified_runtime / video_* / autonomous_runtime / research_* / opportunity_radar | 本轮**不写这些文件**；仅新建文件 + pyproject 追加 3 行 + QUICK_STATE | 单写者规则（章程 §0.2） |

## Cycle 0 — 能力探测与安全快照（18:31–18:45）

| 项 | 实测 |
|---|---|
| C0-1 读写权限 | `D:\AI AGENT\Muse` 可读写（file 工具 + 设备端写入 Python） |
| OS / Python | Windows 11 10.0.26100；设备沙箱 Python 3.12.10；项目 `.venv` = 3.11.9（pyvenv.cfg），启动器损坏（QUICK_STATE ENV-VENV，未变） |
| Node / Rust / FFmpeg / Git CLI | **无法探测**（禁子进程）。静态发现：`ui-prototype/`（vite.config.js）、`video-motion/`、`desktop/src-tauri/`（Cargo.toml + tauri.conf.json）、`server/rust/` |
| `cryptography` | 已在 pyproject `[plugin-security]` 可选依赖中声明 → 使用它**不触发 G1**；无它时走纯 Python RFC 8032 兜底 |
| ACP Agent | 按 R1 不探测 |
| C0-2 权威文档 | QUICK_STATE.md（224 模块矩阵）、蓝图、`docs/` 272 个文件；最高批次号 **B390** → 本轮 **B391** |
| C0-3 安全快照 | `.git/refs/tags/omega-pre-20261125` 与 `.git/refs/heads/agent/omega-build-20261125` → `9d4d86061ec250a9011458c8eac8d5dc7f1c730b`（初始 main HEAD） |
| C0-4 备份 | `.qw-backups/20261125/pyproject.toml.omega-pre.orig`（0f06e255…）、`QUICK_STATE.md.omega-pre.orig`（3c068908…），已登记 MANIFEST.txt |

## Cycle 1 — 基线、静态渗透审计、模块对账、只读 DB 取证（18:45–19:05）

### C1-1 静态审计（设备端 AST 全扫 src/ tests/ scripts/，排除 .venv/__pycache__）
- 文件数：src 232、tests 264、scripts 91。
- **语法错误 4（P0）**——全部在另一 Agent 今日 17:05–17:50 新建、未入 git、无测试的子包 `src/muse/opportunity_radar/`：
  - `collectors.py:36` — 两条 import 被拼接到同一行：`... validate_radar_database_pathfrom .models import RadarConfig, utc_now`。修复 = 保留前半句，删除重复的后半句。
  - `models.py:377` — `object.__setattr__(self, "database_path", validate_radar_database_path(...))` 被插在 dataclass 字段区。修复 = 移入 `__post_init__`，替换 386–389 行的内联校验。
  - `sanitizer.py:156` — `def validate_public_url(...)` 与新函数 `def safe_url_log_identity(...)` 拼在同一行，`validate_public_url` 的函数体（175–206 行）失去了 def 头。修复 = 先放完整的 `safe_url_log_identity`，再在 175 行前恢复 `def validate_public_url(value: Any, *, max_length: int = 4096) -> str:`。
  - `dedup_corroborator.py:188–261` — SCHEMA_VERSION=2 迁移块插入，但删除了**错误范围**：旧 `__init__` 头（174–190 行）残留，且旧 `_row_fingerprint`（或同类方法）开头一段代码**已丢失**（261 行起是孤立的 `title_simhash=row[...]`）。**无法凭推测重建**；仅有 `__pycache__/dedup_corroborator.cpython-314.pyc`（17:13:56，早于损坏）可供作者自行比对。
  - 处理：按 R5 **未修改**，移交该子包作者；等级 `CODE_EXISTS(broken)`。
- 裸 `except:` 0；`TODO/FIXME/XXX` 0；模块顶层 `parse_args`：仅 `scripts/muse-kinetic-explainer.py`、`scripts/muse-site-preview.py`（脚本本身，非库导入副作用，P3）。

### C1-2 模块矩阵对账（Baseline Reconciliation Table）

| 蓝图声称 | 实测 | 差异与根因 |
|---|---|---|
| 221 模块（163 闭环 / 36 部分 / 21 孤岛 / 1 死） | `src/muse/*.py` 顶层 224 个（不含 `__init__`），另有新子包 `opportunity_radar/`（7 个 .py，未计入任何矩阵）；本轮新增 3 个 → 227 | 蓝图写于早期；QUICK_STATE 已更新为 224（173/37/13/1） |
| 21 孤岛 | 严格静态口径（无任何 src 模块导入 **且** 无 pyproject 入口）= **28**：celestial_calendar_provider, contextual_inquiry_cli, creator_media_plan, harness_evolution, herdr_core_release, herdr_events, herdr_transport, interfaces, jyutping, kinetic_explainer, lead_intake, lightpanda_adapter, local_dashboard, main, mempalace_lifecycle, monetization_pilot, open_design_recovery, openrouter, ploid_adapter, product_feedback_import, prosodic_boundary, sandbox_file_manager, scene_analysis, site_preview, social_content_kit, terminal_runtime, usage_observer, zhenyi_bazi_provider | QUICK_STATE 的 13 采用了更宽口径（被 scripts/ 调用或动态导入视为已接线，如 site_preview ← `scripts/muse-site-preview.py`）。两者都如实记录；收敛工作见 BL-WS1-ISL |
| 1 死模块 | `interfaces`（Protocol 声明，0 处导入、0 测试）+ `main`（0 测试导入） | `interfaces` 与 QUICK_STATE 一致；`main` 为 ⚠️ 入口模块 |
| 测试覆盖 | 9 个模块无任何测试直接导入：capability_cli, cyber_cosmos, intent_cli, interfaces, main, progress_cli, research_wiki_review, source_freshness, workload_cli | 与 QUICK_STATE “7 个 ⚠️ CLI 缺专测” 基本一致 |
| 全量测试通过数 | **本轮未测**（R3） | 历史 PASS 数只是历史，不作当前证据 |

### C1-3 生产 DB 只读取证（`data/initiatives/muse.sqlite3`，37,511,168 B）
- 方法：`file:...?mode=ro&immutable=1` 只读打开（云端拷贝上传 37 MB 超时；设备沙箱禁止 `:memory:` 反序列化）；`PRAGMA quick_check` = `ok`。
- 结论（与蓝图 3,954 完全一致）：4,073 行 `creator_initiatives` 中 **3,954 行**各有 1 条 inspiration ref 指向 `C:\Users\ADMINI~1\AppData\Local\Temp\tmp*\reports\autonomous-research-*.md`；
  - 文件已全部不存在；`data/research/reports/`（139 个文件）中**无同名文件**可映射；
  - 3,950 行 `tool/proposed`，4 行 `tool/testing`；创建日期 2026-11-08 → 11-23（11-21 最多 970 行）；
  - 标题含 "Creator-inspired test hypothesis" / "Symbolic candidate: scenario branch"（395 行）；仅有 `created` 事件（`status_changed` 等 = 0）。
- **根因判断**：测试/沙箱运行把报告写进 `TemporaryDirectory`，却把 initiative 写进了**生产** initiatives 库（writer-safety 缺陷：测试未隔离默认 DB 路径）。当前 `autonomous_research.py:3470–3512` 已改为 `stable_report_path or research:<key>`，11-24 后无新增污染。剩余风险：仍需审计“测试默认落生产库”的入口（BL-WS0-WS1）。
- 生产库 SHA-256（取证时）：`329b334f5b05465850b42ee2ed322ef6272f2605d3fc42d1094c7d50d7eb1d11`。

## 主待办（依赖排序）

状态：✅ DONE_SANDBOX（本会话真实执行测试）· 🔶 进行中 · ⬜ 未开始 · ⛔ 被闸门/环境阻塞（附点火命令）

| ID | 工作流 | 项 | 依赖 | 状态 |
|---|---|---|---|---|
| BL-ENV-1 | P0 环境 | 修复 `.venv` 启动器（`Repair-MuseVenv.ps1 -Apply`），之后真机跑全量 unittest + compileall | 用户 | ⛔ 需创作者执行 |
| BL-P0-RADAR | WS0 | `opportunity_radar/` 4 个语法错误（上文 C1-1 精确修复方案） | 子包作者 | ⛔ 单写者规则，移交 |
| BL-WS0-REF | WS0 | inspiration_refs 只读审计 + 备份校验型非破坏修复 CLI | — | ✅ B391 |
| BL-WS0-WS1 | WS0 | 测试写生产库的 writer-safety 审计：搜出所有默认 `data/*/muse.sqlite3` 的测试入口并强制 tmp 路径 | BL-ENV-1 | ⬜ |
| BL-WS1-REV | WS1 P0 | 通用 Ed25519 Reviewer 证明（信任库 SHA-256 钉扎、proposer≠reviewer、绑定 ticket+artifact+test evidence、新鲜度、一次性 nonce、吊销、测试密钥隔离）+ 密钥仪式脚本 | — | ✅ B391 |
| BL-WS1-REV-WIRE | WS1 | 把 `ReviewAttestationVerifier` 接入 `review_gate` / `TaskQueue` 完成态转换（非视频票据） | BL-WS1-REV；review_gate 写入权 | ✅ B392（见下） |
| BL-WS1-ISL | WS1 | 28（严格口径）/13（宽口径）孤岛逐个裁决：接线 / 归档 / 保留为库 | — | ⬜ |
| BL-WS1-FLEET | WS1 | AgentRegistry 实测路由指标 + 角色模板与在线 Agent 分离 | — | ⬜ |
| BL-WS2-10D | WS2 | 十维意识状态（在 inner_state/consciousness_loop 之上，SQLite 持久化） | — | ⬜ |
| BL-WS2-MEMLC | WS2 | 记忆生命周期 / 多模态时间索引 / Obsidian 冲突审计 | — | ⬜ |
| BL-WS3-RES | WS3 | 研究子任务编排 + 来源独立性审计 + Claim Dossier 完整性 | research_* 写入权 | ⬜ |
| BL-WS3-XUAN | WS3 | 玄枢超融合引擎（接入 celestial/zhenyi 孤岛，不可变 symbolic_inspiration 标签） | — | ⬜ |
| BL-WS4-EVO | WS4 | 受控自我进化全链路 + Protected Surface 拦截 + 原子回滚 | BL-WS1-REV-WIRE | ⬜ |
| BL-WS4-PLUG | WS4 | 插件 Ed25519 发布者签名 / 吊销 / 能力代理（可复用 reviewer_attestation 原语） | BL-WS1-REV | ⬜ |
| BL-WS5-FB | WS5 | 反馈导入本地 loopback 端点（CSRF/Origin）+ Dashboard 拖放 | — | ⬜ |
| BL-WS6-* | WS6 | 一键视频编排器、Hook 引擎、摄影指导、AI 超分工作流、粤语声场、VLM 评审 | 另一 Agent 负责 video_*；需协调 | ⬜ |
| BL-WS7-LEDGER | WS7 | 已结算收入 / 净贡献账本 + HMAC 支付 webhook（防重放、幂等、哈希链） | — | ✅ B391 |
| BL-WS7-RADAR | WS7 | Value Radar 例程注册（默认 stopped）+ 单位经济模拟器 + 反证据必填 | BL-P0-RADAR | ⬜ |
| BL-WS7-QUOTE | WS7 | Quote Intelligence CSV/XLSX/PDF 解析（stdlib OOXML） | — | ⬜ |
| BL-WS8-UI | WS8 | 14 个运营中心（ui-prototype / desktop / web-portal） | Node 环境 | ⬜ |
| BL-WS9-SEC | WS9 | SSRF / zip-slip / CSRF 对抗测试套件 | — | ⬜ |
| BL-WS9-IGN | WS9 | `scripts/ignite_live_gates.py`（默认只读）+ Runbook | — | ✅ B391 |

## Cycle 2 — 批次 B391（19:05–19:50）

### B391-1 WS1 P0 `src/muse/reviewer_attestation.py`（新）+ `tests/test_reviewer_attestation.py`（新，17 例）
- 后端：`cryptography`（已声明可选依赖）优先；无则纯 Python RFC 8032。测试含 RFC 8032 §7.1 TEST 1/2/3 向量、s≥L 延展性拒绝、跨后端确定性签名一致。
- 对抗用例：重放、签名期自审 + 手工伪造文档的验证期自审、全零签名（“只有 actor 字符串”）、错误密钥冒用受信 key_id、换 artifact/test-evidence/ticket、篡改 verdict、过期/未来/超长 TTL、ephemeral_test 密钥冒充生产、吊销密钥、信任库钉扎不符/指纹篡改/相对路径、回执表 UPDATE/DELETE 被触发器拒绝、多余/缺失字段、畸形 expected binding。
- 入口：`muse-reviewer-attest sign|verify|backend`（pyproject 已注册）、`scripts/provision_reviewer_keys.py keygen|revoke|show`。

### B391-2 WS0 `src/muse/inspiration_ref_repair.py`（新）+ `scripts/audit_and_repair_inspiration_refs.py`（新）+ `tests/test_inspiration_ref_repair.py`（新，8 例）
- 默认 audit：备份 API（失败回退 `VACUUM INTO`）从 `mode=ro` 连接复制到临时目录，只读副本；源文件前后 SHA-256 对比。
- apply：`BEGIN IMMEDIATE` 持写锁 → 复核哈希 → 字节级备份（SHA-256 必须等于锁定前像 + integrity_check）→ 改写 → 追加式 `inspiration_ref_repairs` 记录原值 → 校验剩余临时引用 = 0 → COMMIT；任何异常整体回滚。生产库需 `--allow-production --confirm-sha256 <当前哈希>`；WAL/journal 非空则拒绝。
- 真实性修复：沙箱运行暴露“权限受限环境下对临时路径 stat 会抛 PermissionError 导致审计崩溃”，已改为计数 `temp_ref_existence_unknown`。
- **生产克隆真机演练**（设备端，`tmp/omega-drill-20261125/drill_report.json`）：audit 3,954/3,954；dry-run 计划 3,954；apply 改写 3,954 行、剩余 0、行数 4,073 不变、`integrity_check_after=ok`、备份 SHA-256 = 前像 `ff3e6a6d…726e`、改写后 `f42d3366…b24b`；二次运行改写 0（幂等）；直接对生产库调用被拒（`RepairRefused … G6`）；**生产库 SHA-256 前后均为 `329b334f…1d11`（未变）**。等级：`LOCAL_SANDBOX_RAN`（真实数据克隆）。

### B391-3 WS7 `src/muse/revenue_ledger.py`（新）+ `tests/test_revenue_ledger.py`（新，9 例）
- 不变式：view/like/lead/inquiry/cart_checkout/pending_order 只计数量（SQLite CHECK 强制金额=0）；`settlement` 必须带 `hmac_webhook`/`provider_statement` 回执（代码 + CHECK 双重，原生 SQL 也绕不过）；`net = settled − refunds − chargebacks − fees − compute/tool costs`；追加式 + 哈希链；`(source, external_id)` 唯一 → 重放幂等、同 ID 不同金额报冲突；空账本 = `US$0.00`。
- Webhook：`t=…,v1=HMAC-SHA256(t.body)`，常量时间比较，±300 s 时间窗，畸形 JSON / 未映射事件 / 伪造签名全拒；`checkout.session.completed`、`charge.succeeded` 只算 pending_order。
- 等级：`LOCAL_SANDBOX_RAN (fake_external_boundary: payment_provider_webhook)`；生产状态：**已结算收入 US$0.00**。

### B391-4 WS9 `scripts/ignite_live_gates.py`（新）+ `docs/HUMAN_IGNITION_RUNBOOK.md`（新）
- 默认/`--check-only` 只读；`--step 1..5` 需输入确认短语。`subprocess` 仅在交互点火时惰性导入。
- 设备端实测 check 函数：Python 3.12.10 PASS；inspiration_refs WARN 3,954（只读）；**ComfyUI 127.0.0.1:8188 只读探测：未运行**（未启动，G3）；LAN 地址已列出；收入 US$0.00。

### B391 验证证据
- 云端沙箱“拼装执行”（项目源文件逐字读取自设备，仅剥离 `from muse… import` 行）：`test_reviewer_attestation` **17/17**、`test_inspiration_ref_repair` **8/8**、`test_revenue_ledger` **9/9** → **34/34 OK**；9 个新文件 `compile()` 全部通过；`pyproject.toml` 经 `tomllib` 解析：75 个脚本入口。
- 测试日志 SHA-256：`75c70fcdbc7e64257ae7a4475742f3adff11f26e4986544ecb790c9ca1694757`
- 制品清单 SHA-256（10 个文件逐一哈希的 canonical JSON）：`337a233c96c069f0cefa2924693d0b4cca194ec4924c4c00795318ee5f5a4178`
- 审查签名演练：ephemeral_test 密钥 `rk-ephemeral-b391`（指纹 `99462e55…239d`），`attestation_sha256 = c25ba729c20ee0cc40e0f8b3b9730328ca53d4f3b71ecf0d647164ba53ab898d`；重放被拒；生产验证器拒收测试密钥。**这是同一执行者的审查轮次，不是独立审查**（R4）。
- 审查轮次发现并当场修复 3 项：expected binding 结构未校验（会抛 TypeError 而非 fail-closed）→ 已加固 + 新测试；临时路径 stat 权限异常；首次 file_edit 被基于旧副本的第二次编辑覆盖 → 改用设备端精确替换并回读校验。
- 未完成/诚实边界：真机 3.11 全量回归未跑（R3）；未 git commit（R2）；新模块尚未接入 review_gate/TaskQueue 主干（BL-WS1-REV-WIRE）。
- Commit：无（见 Runbook §0 的提交命令）。

## Cycle 3 — 批次 B392：签名审查门接入 Trunk B 主干（2026-11-25 19:05–19:30）

### 设计（单 Commander / 单 TaskQueue，不新增状态值、不新增队列）
- `worker.run_once(..., signed_review_required_kinds=..., signed_review_proposer_id=...)`：任务类型在集合内且 handler（及可选模型审查）成功时，Worker **不再调用** `finish(status="succeeded")`，而是计算 binding（`ticket_id`、`canonical_json(result)` 的 SHA-256、`_test_evidence` UTF-8 的 SHA-256、proposer=配置值/agent_id/`muse-worker`，**不信任 payload 自报**），调用 `TaskQueue.finish_pending_signed_review` 停放到 `waiting_approval`。缺 `_test_evidence` 时失败为 `review.test_evidence_missing`。参数已贯通 `run_parallel_once` / `run_forever` / `run_parallel_forever` / CLI `--signed-review-kind`（可重复）、`--signed-review-proposer-id`。默认空集合 = 行为不变。
- `TaskQueue.finish_pending_signed_review`：`BEGIN IMMEDIATE` + 租约围栏（`lease_owner` 匹配且 `lease_expires_at > now`，僵尸 Worker 返回 None），写 `_result` 与 `_signed_review{status:pending,binding}`，事件 `ticket.status_changed` / `report.submitted` / `review.signature_requested`。
- `TaskQueue.complete_signed_review`：只接受真正的 `ReviewAttestationVerifier` 实例（鸭子类型的“永远通过”对象被拒）；回执库必须与任务库不同文件（防同库死锁）；先 `check` 后一次性 nonce 消费；approve→`succeeded`、reject→`failed`+`failure.classified(review.rejected)`；事件 `review.attested|review.rejected`。
- `TaskQueue.approve` 新增守卫：`_signed_review.status=pending` 的票据拒绝重新批准（否则会重新入队重跑 handler、绕过签名）。`cancel` 仍可用。
- 新 CLI `src/muse/signed_review.py` → `muse-signed-review list|export|submit`；export 按字节写出 `artifact.json`/`test_evidence.txt`/`binding.json`，写前复核哈希（停放后结果被篡改 → 拒绝），`open("xb")` 不覆盖；submit 支持 `--trust-store/--trust-store-sha256` 或环境变量。
- 改动前备份：`.qw-backups/20261125/src/muse/task_queue.py.omega-b392.orig`（88e4e39e…）、`worker.py.omega-b392.orig`（b34c552d…），已登记 MANIFEST。

### 验证（云端沙箱：逐字读取设备源码，按模块隔离命名空间加载，仅改写 `from muse…import` 为模块表查找）
- 基线（改动前）：`test_review_gate` 15/15；`test_task_queue` 50/55；`test_worker` 29/42。18 个基线失败全部为沙箱限制（线程数上限、`os.getpid` 被禁、`patch("muse.…")` 需要真实包导入），名单存档 `tmp/b392_baseline_fail.json`。
- 改动后：`test_signed_review_gate` **15/15（新增）**；`test_review_gate` 15/15；`test_task_queue` 55 例失败集合与基线**完全相同**；`test_worker` 42 例失败集合与基线**完全相同** → **新增失败 0**；B391 三套 34/34 仍通过。
- 新测试覆盖的对抗面：Worker 无法直接 succeeded、hash 绑定精确、生产密钥通过并回读回执、签名拒绝→failed、重新批准绕过被拒且 handler 只运行 1 次、提案者自审、跨票据/伪造 artifact 哈希、停放后篡改结果、重放/二次完成、测试密钥生产拒收/沙箱放行、缺测试证据、僵尸/过期租约围栏、伪 verifier、同库回执、取消、非签名类型回归、CLI export→sign→submit 全链路（错误钉扎 fail-closed）、无信任库 fail-closed。
- 测试日志 SHA-256：`33a91ad52f298cdd8f8bb1bd9941ed070f265d5fbee6bf1266dfc700a587d8ad`；制品清单 SHA-256（8 个文件）：`69173bb1273eaac628e3be57ddaa25bac918b951e7b90992118eb2d8fb34182e`；ephemeral_test 签名演练 `attestation_sha256=7f2e83ab067735a68243531df654ce2c47bc5133373ba3b623510b0cdf94d993`（**同一执行者审查轮次，不构成独立审查**，R4）。
- 审查轮次发现并修复：事件 actor 上限 120 而 reviewer_id 允许 128 → 截断；sandbox 中 `compile()` 被 `.__dict__` 过滤器拦截 → 改用 `ast.parse` 做语法校验（非代码缺陷）。
- 诚实边界：`approved_code_change` 在 handler 内已写源码，Worker 层签名来得太晚，不能用此门保护（归入 BL-WS4-EVO）；status_api/Dashboard 尚未单独展示 `_signed_review.status`（票据显示为 waiting_approval）→ 新待办 BL-WS8-REVIEW-UI；真机 3.11 全量回归仍待 BL-ENV-1；未 git 提交。
- 等级：BL-WS1-REV-WIRE → `LOCAL_SANDBOX_RAN`。

## Cycle 4 — 批次 B393 + B398（部分）（2026-11-25 19:22–20:05）

### 授权变更
- 创作者 19:22 明确：电脑上没有其他 Agent 在运行，`opportunity_radar/` 属于已终止的旧会话，授予全仓唯一写入权 → 撤销裁决 R5 中关于该子包的限制。
- 创作者要求：零命令行、禁止选择卡片、真机动作全部封装进根目录的 `.bat`。G6（生产库修复）由创作者双击 `.bat` 触发，视为人工点火。

### B393-1 `opportunity_radar/` 修复（备份：`.qw-backups/20261125/src/muse/opportunity_radar/*.omega-b393.orig`）
- collectors.py：两条 import 被拼到同一行；`fetch_json` 后面重复粘贴了旧版方法尾部 18 行（650–667 行，静态扫描只报了第一个错误）。**安全修复**：请求头注入检查写的是字面量 `"\\r"`/`"\\n"`（两个字符），真实的回车换行能通过 → 改为真实控制字符，并补上 `\x00`。
- sanitizer.py：`SanitizedText` 丢失了 `@dataclass(frozen=True, slots=True)`，构造时会崩溃；`validate_public_url` 和 `safe_url_log_identity` 两个函数头被拼成一行，前者丢失了函数头。**安全修复**：标题全部是 `<script>…</script>` 时，旧的回退逻辑会把脚本内容当作文本泄漏出来（测试发现）→ 解析成功但没有可读内容时返回空。
- models.py：`RadarConfig` 丢失 6 个字段（max_text_chars / max_retries / retry_base_seconds / min_host_gap_seconds / host_concurrency / cache_ttl_seconds，默认值按 `from_env` 恢复）；字段区多插了一行 `__post_init__` 代码；`from_env` 有两个参数挤在同一行。数据库路径校验统一改用 `validate_radar_database_path`。
- dedup_corroborator.py：删除残留的 v1 `__init__` 片段；补上 `close` / `__enter__` / `__exit__`；按调用方 `_from_row(row)` 的用法和表结构，重建丢失的 `_from_row` 开头；删除 `_merge` 里孤立的 `}`。
- 验证：`test_opportunity_radar_repair` **13/13**（配置、SSRF/凭据/私网拦截、提示词注入与密钥脱敏、URL 归一化、同源合并、`independent_source_count` 永远为 0、v2 迁移、拒绝更新版本的库、拒绝写入业务库）；设备端全仓 AST 扫描 **599 个 .py，语法错误 0**。

### B393-2 核查后不重写
- `video_task_worker.run_approved_ticket`：已校验精确派发身份、活跃租约和过期时间、Commander DAG/审批绑定、Attempt 匹配，有心跳，`tests/test_video_task_worker.py` 有约 46 个相关测试。重写属于无效劳动，而且有回归风险。
- Value Radar 例程：`autonomous_runtime` 已登记 `daily-value-radar`，并带有经过清洗的报告加载器。

### B393-3 WS7 新模块
- `unit_economics.py`：精确 Decimal 计算（拒绝浮点）；四条反证必填；止损线必须事先写好（浏览量不能作指标；到期未测量视为触发）；输出带 `simulation_not_forecast` / `price_hypothesis` 标签，`revenue_recognised="0"`。
- `quote_ingest.py`：只用标准库读取 CSV（utf-8-sig / gb18030）、XLSX（zipfile + ElementTree，支持共享字符串和内联字符串，拦截压缩比大于 200 的压缩炸弹）、文字版 PDF（Flate/未压缩流里的 Tj/TJ 指令）；每行带出处；PDF 可信度为 low；扫描件标记需要 OCR；输出直接交给 `audit_quotes`。
- `test_unit_economics_quote_ingest` **9/9**。

### B398-1 待签名审查台（解决 BL-WS8-REVIEW-UI）
- `review_desk.py`：密钥库放在 `%USERPROFILE%\MuseReviewerKeys`（拒绝放在项目内）；私钥只存密文（标准库 scrypt N=2^15 + HMAC-SHA256 计数器密钥流 + 先加密后认证）；每次签名都要输入口令；连续 5 次错误锁定 60 秒；签名前复核结果和测试日志的指纹；用生产验证器 + 一次性 nonce + 独立回执库调用 `TaskQueue.complete_signed_review`。
- status_api（备份 `status_api.py.omega-b398.orig`）新增：`GET /api/v1/signed-reviews`、`GET /api/v1/signed-reviews/<id>`、`GET /api/v1/reviewer-key/status`、`POST /api/v1/reviewer-key/initialize`（确认短语 `INIT-REVIEWER-KEY`）、`POST /api/v1/signed-reviews/<id>/decide`（确认短语 `SIGN-REVIEW`）。沿用原有的仅本机访问 + Bearer + Origin + 只收 JSON + 8 KiB 上限。
- 界面：`ui-prototype/review-desk.{html,js,css}`，已登记到 vite 构建入口，并直接复制进 `dist/`。符合 CSP（没有内联脚本和内联样式），不可信数据只用 `textContent` 渲染。
- `test_review_desk` **9/9**（加解密与篡改检测、初始化规则、一键通过/拒绝、错误口令锁定（假时钟）、篡改结果拒签、信任库被改则钉扎失效、静态契约：JS 引用的元素 id 都存在、API 路由都存在、确认短语两端一致、vite 已登记）。

### B398-2 一键验收
- `1_双击一键验收并保存核心代码.bat`：1616 字节，CRLF，无 BOM，首行 `@echo off` + `chcp 65001 >nul`；自动探测/修复 `.venv` → `py -3.11` → `python`。
- `scripts/autopilot_stage1_verify_and_commit.py`：备份全部生产库（只读源 + SQLite backup API + quick_check + SHA-256 清单）→ inspiration_refs 审计 + 无损修复（Muse 正在写时自动跳过）→ 真机全仓 unittest + Omega 新套件 + 沙箱跑不了的 3 个套件 + compileall → npm test/build（有 npm 时）→ 点火体检 → 切到 `agent/omega-build-20261125`，按明确清单提交（自动排除 .env、sqlite、`__pycache__`；**从不 push**）→ 写 `reports/autopilot/latest.json` 和 `latest.md`。日志解析器已在沙箱验证。

### 回归
- 新增测试累计 **80 例全部通过**（17+15+8+9+13+9+9）；`test_review_gate` 15/15；`test_task_queue` / `test_worker` 的失败集合与 B392 基线完全一致（18 例沙箱限制）→ **新增失败 0**。日志 SHA-256 `f3f2cd36f32e1e4984de22912f73190790b29a5788fd5ddb71b8f7c7b1fb1952`。
- `status_api.py` 只做了 AST 校验，它的现有测试要真机跑（依赖太多，云端沙箱加载不了），由 `.bat` 覆盖。

### 安全发现（需要创作者处理）
- `.git/config` 的 `remote "origin"` URL 里以明文嵌着一个 GitHub 个人访问令牌（ghp_…，本账本不记录令牌值）。建议到 GitHub → Settings → Developer settings 吊销它，并把 remote 改回不含令牌的 URL。autopilot 从不 push，也从不打印 remote。

### 未完成（下一轮按顺序做）
B394（自我进化：补丁写入前签名 + 受保护面拦截 + 原子回滚 + 假时钟熔断测试；插件 Capability Broker）→ B395（十维意识内核、记忆生命周期）→ B396（研究子任务编排、来源独立性去重、玄枢引擎、反馈回传接口）→ B397（视频算法引擎）→ B398 其余部分（孤岛清零、14 个中心界面、SSRF/zip-slip/CSRF 安全测试套件、主控台首页审查台入口）。


## Cycle 5 — 批次 B394–B398（2026-11-25 19:54–）
- 读取真机报告 20261125-195223：omega_new 80 例中 3 个 ERROR（WinError 32，测试里 `with sqlite3.connect` 未关闭）→ `contextlib.closing` 修复；full 套件 0 运行（`-t .` 需 tests 包）→ 改 `-p test_*.py`。inspiration_refs 已真机修复 3,954 行；Git 已提交 5a43e07（未 push）。
- `.git/config`：已备份到 `.qw-backups/20261125-b394/`，remote URL 去除内嵌凭据，现为 `https://github.com/<owner>/muse.git` 形式；`.gitignore` 增加 `.qw-backups/`。**旧令牌仍需在 GitHub 吊销**（备份文件内仍含旧值，勿外传）。
- 新增 12 个模块、5 个测试文件（60 例全绿）、6 个页面 + 2 个共享资源；改动 evolution_executor / worker / autonomous_runtime / status_api / App.svelte / vite.config.js / pyproject / autopilot / .bat，全部先备份到 `.qw-backups/20261125-b394/`。设备端 AST 校验：语法错误 0。
- 等级：BL-WS4-EVO → `LOCAL_SANDBOX_RAN`（签名门在任何写入/测试之前执行；worker `--enable-evolution` 无信任库即拒绝启动）。
- 诚实边界：未连 ComfyUI/GPU（只生成并校验工作流 JSON，预检不通过就不提交）；VLM 只是建议，发布永远需人工；紫微/奇门/六壬需真机已装引擎，否则显示 unavailable；Status API 与 Svelte 构建由 `.bat` 真机验证。
