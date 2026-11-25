# MUSE 项目地图（MUSE_PROJECT_MAP.md）

> 生成时间：2026-11-25 · 生成方式：只读静态分析（AST 解析全部 `src/muse/*.py`、`tests/`、`scripts/`，未执行项目代码）
> 范围说明：仅分析项目自有代码；`third_party/`、`external/`、`tts-engines/`、`.venv/`、`node_modules/`、模型权重等第三方/运行态内容只做体量统计。
> 敏感文件（`config/muse.toml`、`config/model-runtime.json`、`config/opencode.local.jsonc`、`.env*`）**未读取内容**。

## 1. 总体概况

| 项 | 值 |
|---|---|
| 目录总量 | 约 148,378 个文件 / 44.4 GB（绝大部分是虚拟环境、TTS 引擎、模型、第三方代码与运行数据） |
| 项目自有核心代码 | `src/muse/`：220 个模块，约 89,700 行 Python |
| 测试 | `tests/`：262 个 unittest 文件（README 记载 B385 基线 2,475 项通过） |
| 脚本 | `scripts/`：134 个（PowerShell / Python / cmd） |
| CLI 入口 | `pyproject.toml` 定义 65 个 `muse-*` 命令，全部指向存在的模块与 `main()`（已逐一核对） |
| 技术栈 | Python ≥3.11（核心仅标准库 + SQLite）；Node/TypeScript（`ui-prototype/`、`web-portal/`）；Rust + Tauri 2（`desktop/src-tauri/`、`server/rust/`）；FFmpeg / ComfyUI(Wan 2.1) / Remotion / Piper / 粤语 TTS·ASR（视频与语音） |
| 运行环境 | Windows（主力，`.venv` 为 Python 3.11.9）；另有 Linux x86_64 隔离副本与 Debian 包 |
| 版本控制 | Git（`main` 分支），`.gitignore` 排除 data/artifacts/models/vault/大媒体/第三方 |

## 2. 顶层目录树与职责

```
D:\AI AGENT\Muse
├── src/muse/            ★ 核心运行时（220 模块，见第 4 节）
├── src/data/            运行态 SQLite（autonomy/initiatives/memory/research/tasks 等）
├── tests/               ★ unittest 回归（262 文件）
├── scripts/             ★ 启停、桌面、视频、宣传片、GitHub 同步、反馈导入、WebCodex 执行器
├── config/              配置样例（*.example.*）+ 本机私有配置（未读取）
├── docs/                宪章、架构、路线图、逐批进度（PROGRESS*.md）、事实核验底稿（271 文件）
├── web-portal/          本机浏览器控制台静态页（index.html/app.js/sw.js/PWA）
├── ui-prototype/        Node/TS 只读桌面 UI 原型（含 node_modules，72 MB）
├── desktop/             Tauri 2 + Rust 桌面壳（14 GB，主要是 Rust target 构建产物）
├── server/rust/         Rust 服务端组件
├── ember-fire/          原创竖屏宣传片《火种不灭》工程（build.ps1/mix.py/qa.py/package.py）
├── pipeline/            早期占位视频流水线（README 已标注弃用，仅作历史）
├── tools/               场景标注/基准评测/视频运动检查小工具
├── workflows/           ComfyUI Wan2.1 工作流 JSON
├── deploy/systemd/      Linux systemd 单元
├── plugins/、sandbox/   插件样例（执行默认关闭）、沙箱动作日志
├── examples/            quote_intelligence 示例
├── vault/               Obsidian 只读镜像
├── data/ (9.3 GB)       运行数据（任务库、研究、产品、媒体）
├── artifacts/、outputs/、reports/   生成产物、成片、报告
├── models/ (968 MB)、tts-engines/ (12.6 GB)、third_party/ (2 GB)、external/   模型与第三方
├── RuiC-oc-ferry/、dsh-our-free-model/、video-motion/   外部/相关子项目副本
├── .venv/、.muse-*/、.tools/、.swarm/、.claude-flow/   环境与 Agent 运行态
├── logs/、tmp/、tmp3s0ocv0y/   日志与临时目录
├── README.md、AGENTS.md、pyproject.toml、.gitignore
└── 根目录杂项：`=`、`@{consecutive_link_misses=0}`、`agent-*-schema.txt`、`*.jpg`、`full-suite.log`（疑似误生成，见 ISSUES.md）
```

### 体量排行（前 12）
| 目录 | 文件数 | 体积 |
|---|---:|---:|
| desktop | 23,805 | 14.4 GB |
| tts-engines | 57,162 | 12.6 GB |
| data | 2,389 | 9.3 GB |
| .muse-video-pilot-workspace-20261124 | 2,675 | 2.3 GB |
| third_party | 17,594 | 2.0 GB |
| models | 8 | 968 MB |
| RuiC-oc-ferry | 8,413 | 597 MB |
| video-motion | 15,066 | 486 MB |
| outputs | 433 | 367 MB |
| .muse-canto-tts-py314 | 3,876 | 315 MB |
| dsh-our-free-model | 928 | 280 MB |
| src | 865 | 28 MB |

## 3. 入口、依赖与配置文件

| 类别 | 文件 | 说明 |
|---|---|---|
| 主入口 | `src/muse/main.py` | `python -m muse.main --check`：校验 `config/muse.toml`，不启动服务 |
| 统一运行时 | `src/muse/unified_runtime.py` | `health / memory-search / swarm-init / dispatch-once / commander-once / desktop-snapshot` |
| 任务执行 | `src/muse/worker.py`、`task_cli.py` | Worker 轮询 TaskQueue；task_cli 审批/取消/列表/入队转码 |
| 自主运行 | `src/muse/autonomous_runtime.py` | `run / once / research-once / status / pause / resume / stop / enqueue` |
| 界面服务 | `status_api.py`、`local_dashboard.py`、`web_portal.py` | 本机状态 API（Bearer）、LAN 浏览器宿主、HTTPS Portal |
| 开发执行器 | `scripts/muse_dev.py` → `scripts/webcodex_psmux_exec.py` | AGENTS.md 规定的 Windows 命令通道 |
| Windows 启停 | `scripts/Muse-Desktop.ps1`、`Muse-Stop.ps1`、`Install-Muse-Shortcut.ps1` | 日常使用入口 |
| 依赖声明 | `pyproject.toml` | 必选仅 `tzdata`（Windows）；可选组：plugin-security / web-scraping / lightpanda / video-diagnostics |
| 前端依赖 | `ui-prototype/package.json`、`desktop/` | Node / Tauri |
| 配置样例 | `config/muse.example.toml`、`video_factory.example.toml`、`agent-discovery.example.json`、`intake-*.example.json` | 可入库 |
| 私有配置 | `config/muse.toml`、`model-runtime.json`、`opencode.local.jsonc` | 已被 .gitignore 排除，**本次未读取** |
| 说明文档 | `README.md`、`AGENTS.md`、`docs/ARCHITECTURE.md`、`docs/VISION.md`、`docs/PROGRESS.md` | |

## 4. 核心模块职责（src/muse）

图例：LOC=行数；被依赖=被多少个 muse 模块导入；CLI=有 `main()`；⚠=当前无法导入。


### 4.1 Commander / 任务账本 / Worker（调度核心）（41 个）

| 模块 | LOC | 被依赖 | CLI | 职责（模块 docstring） |
|---|---:|---:|:-:|---|
| `task_queue` | 1506 | 34 |  | Durable SQLite queue for resumable Muse tasks. |
| `commander_planner` | 341 | 10 |  | Validated Commander DAG planning with a deterministic safe fallback. |
| `gpu_resources` | 468 | 8 |  | Cross-process GPU arbitration for Muse-owned inference workloads. |
| `ruflo_adapter` | 300 | 8 | ✓ | Safe bridge to an installed Ruflo CLI/MCP engine. |
| `agent_registry` | 472 | 7 |  | Durable metadata, heartbeat and evidence-based outcomes for trusted Muse agents. |
| `review_gate` | 219 | 6 |  | Independent, structured review gate for agent-produced artifacts. |
| `lease_recovery` | 390 | 5 | ✓ | Read-only lease diagnostics and additive, backup-first lease schema migration. |
| `process_utils` | 19 | 5 |  | Small cross-platform helpers for quiet, explicitly managed child processes. |
| `resource_governor` | 180 | 4 |  | Conservative host-resource probing and bounded worker concurrency decisions. |
| `orchestration` | 512 | 3 |  | Provider-neutral multi-agent fan-out and dependency-DAG execution with audit records. |
| `unified_runtime` | 1907 | 3 | ✓ | Single Muse runtime boundary for durable tickets, Ruflo execution and desktop observation. |
| `workload` | 76 | 3 |  | Deterministic hourly workload estimation and capacity planning. |
| `adapter_handlers` | 66 | 2 |  | TaskQueue handlers that execute approved, registered tool adapters. |
| `artifact_evidence` | 152 | 2 |  | Fail-closed verification for local workflow deliverables. |
| `autonomous_runtime` | 1393 | 2 | ✓ | Local supervisor for bounded Muse workflows and evidence-grounded research. |
| `routines` | 547 | 2 | ✓ | Durable, foreground routine scheduler with leases and explicit handlers. |
| `adapters` | 126 | 1 |  | Permission-aware adapters that turn registered capabilities into real tools. |
| `agent_discovery` | 214 | 1 | ✓ | Safe discovery of explicitly configured local Agent health endpoints. |
| `daemon_control` | 341 | 1 |  | Bounded local lifecycle control for the Muse autonomous runtime daemon. |
| `failure_taxonomy` | 109 | 1 |  | Stable, privacy-conscious failure classification for Muse task execution. |
| `handoff` | 115 | 1 | ✓ | Build a durable, self-contained prompt for resuming Muse development. |
| `herdr_core_release` | 59 | 1 |  | Pinned Herdr Core release metadata used by the opt-in Windows smoke test. |
| `herdr_events` | 206 | 1 |  | Pure, fail-closed reducer for herdr-core JSON-RPC event notifications. |
| `herdr_transport` | 471 | 1 |  | Existing-Core-only, read-only Herdr Core event subscription transport. |
| `lease_manager` | 387 | 1 |  | Fenced lease mutations for Muse's existing Routine and Runtime stores. |
| `local_agent_adapter` | 80 | 1 |  | Bounded HTTP adapter for explicitly configured loopback Agent services. |
| `pipeline_control` | 166 | 1 |  | Legacy video pipeline compatibility surface; direct subprocess dispatch is disabled. |
| `process_limits` | 98 | 1 |  | Best-effort-independent OS process-tree limits for isolated experiments. |
| `specialist_router` | 565 | 1 |  | Explainable specialist matching and dispatch for the Muse command center. |
| `watchdog` | 258 | 1 | ✓ | Local health checks and periodic workload reporting for Muse. |
| `worker` | 872 | 1 | ✓ | Explicit-handler worker and stoppable scheduler for the Muse task queue. |
| `candidate_enqueuer` | 528 | 0 | ✓ | Idempotently connect creator-inspired claims to the existing Commander TaskQueue. |
| `herdr_adapter` | 77 | 0 | ✓ | Optional Herdr Program Status Protocol (OSC 7501) bridge for Muse Agents. |
| `herdr_core_smoke` | 227 | 0 | ✓ | Opt-in, read-only smoke test for an already-running Herdr Core on Windows. |
| `idle_watch` | 157 | 0 | ✓ | Adaptive local inactivity watcher for Muse progress (not ChatGPT turn telemetry). |
| `interfaces` | 25 | 0 |  | Typed boundaries for future integrations; no external actions occur here. |
| `model_supervisor` | 330 | 0 | ✓ | Supervise Muse's explicitly configured local llama.cpp service. |
| `task_cli` | 90 | 0 | ✓ | Command-line controls for queueing local media work and approving tasks. |
| `terminal_runtime` | 250 | 0 |  | Provider-neutral, read-only terminal runtime discovery. |
| `trace` | 132 | 0 |  | Evidence-linked causal tracing stored in the existing TaskQueue event log. |
| `workload_cli` | 34 | 0 | ✓ | Show the next hourly work batch from Muse's durable task queue. |

### 4.2 记忆 / 对话 / 模型接入（35 个）

| 模块 | LOC | 被依赖 | CLI | 职责（模块 docstring） |
|---|---:|---:|:-:|---|
| `model_client` | 157 | 15 |  | OpenAI-compatible local model adapter (for LM Studio and compatible servers). |
| `memory` | 607 | 11 |  | SQLite-backed, user-auditable long-term memory for Muse. |
| `model_call_context` | 124 | 11 |  | Task-scoped model-call attribution for local provider telemetry. |
| `local_planner` | 96 | 8 |  | Loopback-only LM Studio planner with memory-grounded symbolic context. |
| `conversations` | 85 | 5 |  | Persistent per-thread conversation checkpoints for Muse. |
| `model_service` | 72 | 4 |  | Safe health probing for Muse's configured local model service. |
| `prompt_engine` | 224 | 3 | ✓ | Safe, provider-aware prompt construction for Muse. |
| `reminders` | 441 | 3 | ✓ | Local, persistent reminders with explicit acknowledgement and idempotent creation. |
| `companion_security` | 623 | 2 |  | Host-side pairing and request authentication primitives for Muse Companion. |
| `gemma_intent` | 379 | 2 |  | Opt-in, lazy GGUF intent assistance; never an execution or authorization component. |
| `intent` | 174 | 2 |  | Conservative natural-language intent routing for Muse. |
| `provider_policy` | 124 | 2 |  | Fail-closed routing policy for explicitly verified free provider quotas. |
| `providers` | 384 | 2 |  | Provider-neutral chat contracts, capability routing and adapter registry. |
| `usage_ledger` | 306 | 2 |  | Append-only, source-aware model usage ledger. |
| `assistant` | 118 | 1 |  | Small chat orchestration layer joining local models, memory and persistent threads. |
| `config` | 23 | 1 |  | Configuration loading with Python's standard-library TOML parser. |
| `memory_extraction` | 63 | 1 |  | Opt-in local-model extraction of reviewable memory candidates. |
| `mempalace_adapter` | 347 | 1 |  | Optional, local-only MemPalace bridge for curated Muse memory recall. |
| `mempalace_projection` | 390 | 1 |  | Auditable, manually approved Agent Track projection outbox for MemPalace. |
| `model_capabilities` | 341 | 1 |  | Provenance-aware model capability records and offline prompt conformance checks. |
| `obsidian_cli` | 136 | 1 |  | Read-only adapter for the official Obsidian CLI. |
| `obsidian_vault` | 482 | 1 |  | Publish approved Muse memories as ordinary, Obsidian-friendly Markdown notes. |
| `__init__` | 3 | 0 |  | Muse personal AI companion scaffold. |
| `chat_cli` | 113 | 0 | ✓ | Interactive one-turn chat CLI backed by persistent local conversation threads. |
| `chatanywhere_smoke` | 120 | 0 | ✓ | One-shot, non-persistent ChatAnywhere -> Ruflo -> Muse execution smoke test. |
| `chatgpt_bridge` | 584 | 0 | ✓ | Narrow local bridge entry point for ChatGPT -> WebCodex Runner -> Muse. |
| `companion_transport` | 375 | 0 | ✓ | Explicitly configured TLS transport for read-only Muse Companion access. |
| `intent_cli` | 46 | 0 | ✓ | CLI for previewing a safe Muse execution plan from a natural-language request. |
| `main` | 41 | 0 | ✓ | Minimal command-line entry point for Muse. |
| `memory_cli` | 215 | 0 | ✓ | Command-line interface for inspecting and managing Muse memories. |
| `memory_reflection` | 90 | 0 | ✓ | Conservative cross-memory reflection; all generated insights remain pending. |
| `mempalace_lifecycle` | 232 | 0 |  | Verified, offline backup and no-overwrite restore for a local MemPalace directory. |
| `openrouter` | 160 | 0 |  | OpenRouter free-model adapter with a live, fail-closed quota check. |
| `skillify` | 180 | 0 |  | Evidence-backed Skillify candidates using Muse's existing Agent memory track. |
| `usage_observer` | 99 | 0 |  | Optional adapter decorator that records safe model-usage metadata. |

### 4.3 研究 / 知识图谱 / 自主研究（48 个）

| 模块 | LOC | 被依赖 | CLI | 职责（模块 docstring） |
|---|---:|---:|:-:|---|
| `research_knowledge` | 2175 | 23 |  | Persistent typed research knowledge graph for Muse. |
| `progress` | 339 | 16 |  | Privacy-conscious structured progress events for Muse workflows. |
| `capabilities` | 163 | 12 |  | Persistent registry of verified local tools and Muse capabilities. |
| `research_fetch` | 433 | 9 | ✓ | Bounded public-web source reader for Muse's evidence-backed research workflow. |
| `improvement` | 826 | 8 |  | Durable, approval-gated self-improvement proposal ledger. |
| `autonomous_research` | 3758 | 4 |  | Bounded idle research with private creator context isolated from public evidence. |
| `contextual_inquiry` | 236 | 4 |  | Privacy-conscious, evidence-aware context inquiry contracts. |
| `contextual_inquiry_security` | 368 | 4 |  | Windows DPAPI protection for short-lived Muse contextual case payloads. |
| `experiment_bridge` | 340 | 4 |  | Reproducible experiment manifests and evidence-linked result recording. |
| `philosophy` | 165 | 4 | ✓ | Muse's evolving philosophy: durable principles distilled from experience. |
| `research` | 542 | 4 | ✓ | Read-only web search adapter for a configured SearXNG JSON endpoint. |
| `value_forge` | 404 | 4 | ✓ | Evidence-aware value discovery patterns and bridges for Muse. |
| `autonomy` | 360 | 2 | ✓ | Approval-gated autonomous planning for the local Muse workspace. |
| `business_opportunities` | 523 | 2 | ✓ | Evidence-aware local business opportunity ledger for Muse. |
| `capability_radar` | 455 | 2 | ✓ | Generate ranked, approval-gated improvement proposals from redacted aggregates. |
| `contextual_inquiry_bridge` | 324 | 2 |  | Explicit bridge from contextual inquiry to existing Muse stores. |
| `ego_browser` | 627 | 2 |  | ego-lite browser adapter for bounded, provenance-preserving web research. |
| `evolution_executor` | 897 | 2 |  | Approved, bounded source patches with test evidence and hash-guarded rollback. |
| `research_pipeline` | 677 | 2 | ✓ | Concurrent, evidence-preserving research pipeline for Muse. |
| `source_identity` | 388 | 2 |  | Conservative HTML metadata extraction and reviewable source-identity hints. |
| `autonomous_value_radar` | 279 | 1 |  | Daily evidence-aware value radar for Muse's existing Commander routines. |
| `bookmark_sources` | 129 | 1 |  | Interest-led Chrome bookmark discovery for Muse research. |
| `browser_research` | 241 | 1 |  | Public-research adapters that augment Muse's existing search and source reader. |
| `contextual_inquiry_local_review` | 188 | 1 |  | Explicit local-only model review for encrypted contextual inquiries. |
| `evolution_journal` | 171 | 1 |  | Durable crash-recovery journal for bounded source-edit transactions. |
| `improvement_patch_planner` | 219 | 1 |  | Prepare human-reviewable code-change Tickets from approved improvement proposals. |
| `intake` | 477 | 1 |  | Local, policy-gated intake of external or proactive agent triggers. |
| `research_business_bridge` | 263 | 1 |  | Bridge research reports into evidence-aware business pilot candidates. |
| `research_task_handler` ⚠ | 292 | 1 |  | Commander Worker handlers for evidence, counter-evidence and claim assessment. |
| `research_wiki_audit` | 137 | 1 | ✓ | Read-only Research Wiki governance audit; never initializes or mutates the database. |
| `research_wiki_citation` | 91 | 1 | ✓ | Read-only citation anchor verifier for Research Wiki source snapshots. |
| `scrapling_adapter` | 118 | 1 |  | Optional Scrapling parser integration for Muse's evidence-backed research. |
| `source_freshness` | 125 | 1 |  | Re-fetch reviewed source citations and append fail-closed freshness observations. |
| `test_runner` | 357 | 1 | ✓ | Run unittest suites and record privacy-minimal lifecycle events. |
| `verification` | 124 | 1 |  | Bounded verify→fix→reverify primitive; never schedules work or widens authority. |
| `website_knowledge` | 255 | 1 |  | Persistent, provenance-aware website catalogue for Muse research. |
| `capability_cli` | 28 | 0 | ✓ | Inspect the persistent Muse capability registry. |
| `contextual_inquiry_cli` | 214 | 0 | ✓ | Consent-gated CLI for contextual inquiry tasks with optional encrypted case storage. |
| `harness_evolution` | 454 | 0 |  | Deterministic, local-first harness snapshots and candidate validation. |
| `improvement_cli` | 135 | 0 | ✓ | Command-line interface for reviewing Muse self-improvement proposals. |
| `intake_runtime` | 434 | 0 | ✓ | Pluggable, local-first source intake with structured assessment and durable dispatch. |
| `lead_intake` | 390 | 0 |  | Local-only, consent-gated inquiry intake for the Muse showcase. |
| `lightpanda_adapter` | 268 | 0 |  | Optional Lightpanda CDP adapter for bounded public-page research. |
| `progress_cli` | 32 | 0 | ✓ | Read-only CLI for recent Muse progress events. |
| `quote_intelligence` | 366 | 0 | ✓ | Offline, source-grounded supplier quotation comparison for Muse. |
| `research_wiki_review` | 110 | 0 | ✓ | Human-readable, read-only evidence review queue for Research Wiki. |
| `research_wiki_review_record` | 100 | 0 | ✓ | Explicit, append-only evidence review writer with verified citation anchors. |
| `source_identity_cli` | 46 | 0 | ✓ | CLI for inspecting and reviewing source-identity candidates. |

### 4.4 灵感 / 玄学象征引擎（15 个）

| 模块 | LOC | 被依赖 | CLI | 职责（模块 docstring） |
|---|---:|---:|:-:|---|
| `calendar_context` | 246 | 6 |  | Shared, explicit calendar context contract for Muse metaphysics engines. |
| `sexagenary` | 101 | 5 |  | Deterministic sexagenary-cycle arithmetic with explicit reference vectors. |
| `inspiration` | 1122 | 4 | ✓ | Memory-grounded symbolic inspiration briefs for Muse. |
| `yijing` | 221 | 2 |  | Deterministic, source-aware I Ching three-coin line calculations. |
| `consciousness_engine` | 413 | 1 | ✓ | Phase 5 Muse consciousness / philosophy evolution engine. |
| `cyber_cosmos` | 1270 | 1 |  | Read-only aggregation and symbolic-hypothesis views for the Muse Cyber-Astrolabe client. |
| `inspiration_evolution` | 1325 | 1 |  | End-to-end closed loop: Inspiration -> Research -> Improvement -> Reviewable Creator Brief |
| `inspiration_productization` | 276 | 1 |  | Safely turn an explicitly selected offline inspiration into a local prototype. |
| `kinliuren_provider` | 166 | 1 |  | Isolated adapter for the pinned Kinliuren 0.1.2.9 day-chart engine. |
| `mingli_provider` | 223 | 1 |  | Optional local Mingli-AI (真一命理) OpenAI-compatible interpretation adapter. |
| `qimen_provider` | 266 | 1 |  | Isolated adapter for the pinned qimen-rs 0.2.1 calculation engine. |
| `ziwei_adapter` | 371 | 1 |  | Optional, provenance-preserving adapter for the third-party Zi Wei knowledge catalog. |
| `ziwei_provider` | 305 | 1 |  | Isolated adapter for the pinned Zi Wei 0.4.1 chart calculator. |
| `celestial_calendar_provider` | 254 | 0 |  | Optional celestial-calendar provider for Muse's shared CalendarContext. |
| `zhenyi_bazi_provider` | 246 | 0 |  | Isolated local adapter for the MIT-licensed Zhenyi-Bazi deterministic chart engine. |

### 4.5 产品 / 原型 / Initiative（23 个）

| 模块 | LOC | 被依赖 | CLI | 职责（模块 docstring） |
|---|---:|---:|:-:|---|
| `initiative_engine` | 2272 | 19 |  | Muse's proactive idea and product initiative incubator. |
| `product_builder` | 881 | 4 | ✓ | Build a self-contained offline product from a Muse initiative. |
| `software_crafting` | 1412 | 4 | ✓ | Evidence-led software recreation and creation adapters for Muse Commander. |
| `workflow_profiles` | 778 | 4 |  | Versioned workflow profiles and ticket-backed lifecycle for Muse Commander. |
| `open_design_evidence` | 194 | 2 |  | Persist bounded Open Design artifact metadata into Muse's existing Research Wiki. |
| `plugin_platform` | 1065 | 2 | ✓ | Declarative plugin registry for Muse's Commander-owned capability ecosystem. |
| `clip_product_adapter` | 250 | 1 |  | Register rendered highlight clips as traceable Muse video products. |
| `creative_studio_api` | 392 | 1 | ✓ | Creative Studio API backing functions for the Muse video studio. |
| `desktop_adapter` | 132 | 1 | ✓ | Permission-aware bridge to the pinned agent-desktop native CLI. |
| `initiative_feedback_bridge` | 168 | 1 |  | Idempotent bridge from creator review feedback to Research Wiki and capability signals. |
| `open_design` | 1099 | 1 | ✓ | Local Open Design MCP bridge owned and scheduled by Muse Commander. |
| `skill_fusion` | 556 | 1 |  | Safe source-to-capability fusion for Muse. |
| `social_campaign_tracking` | 105 | 1 |  | Deterministic, offline UTM attribution drafts for manual social distribution. |
| `software_recreation` | 226 | 1 | ✓ | Plan-only software study/recreation workflow. |
| `feedback_cli` | 51 | 0 | ✓ | Explicit structured feedback intake for Muse capability discovery. |
| `monetization_pilot` | 462 | 0 | ✓ | Build a local Cantonese-narrated, review-only video package without production DB writes. |
| `open_design_recovery` | 74 | 0 |  | Persistence-only recovery for completed Open Design runs. |
| `ploid_adapter` | 455 | 0 |  | Optional Ploid MCP adapter for evidence-backed professional people research. |
| `product_feedback_import` | 227 | 0 |  | Import a user's offline Muse product worksheet as an unverified experiment record. |
| `prototype_builder` | 388 | 0 | ✓ | Create reviewable static prototypes from approved local initiatives. |
| `sandbox_file_manager` | 322 | 0 |  | Sandbox file operations with scoped approval grants and hash-chained audit logs. |
| `site_preview` | 163 | 0 |  | — |
| `social_content_kit` | 474 | 0 |  | Create an offline, rights-aware social content kit from an existing Muse publish pack. |

### 4.6 视频 / 语音 / 媒体生产线（52 个）

| 模块 | LOC | 被依赖 | CLI | 职责（模块 docstring） |
|---|---:|---:|:-:|---|
| `media` | 325 | 12 | ✓ | Local media-tool discovery, task routing and safe AMD AMF transcoding. |
| `video_product_builder` | 2917 | 8 | ✓ | Build versioned Muse video products from local assets or local Wan prompts. |
| `video_engines` | 429 | 7 |  | Safe loopback-only discovery clients for ComfyUI and Velorn MCP. |
| `video_workflow` | 183 | 7 |  | Strict storyboard validation and deterministic video-production DAG planning. |
| `video_studio` | 748 | 5 | ✓ | OpenMontage-inspired video production profiles and plan-only Commander DAGs. |
| `cantonese_tts` | 628 | 4 | ✓ | Pinned, local-only Cantonese TTS adapter for Muse narration. |
| `narration_generator` | 325 | 4 |  | Local, deterministic Cantonese narration generation for Muse Phase 2. |
| `story_bible` | 279 | 4 |  | Versioned story-bible and continuity contracts for Muse video production. |
| `subtitle_review` | 298 | 3 |  | — |
| `video_capabilities` | 271 | 3 |  | Local video capability probes with health and capability reported independently. |
| `video_contact_sheet` | 127 | 3 |  | Generate bounded local video contact sheets for human visual review. |
| `video_human_review` | 412 | 3 |  | Append-only, scope-bound human visual review ledger for video products. |
| `video_prompt_composer` | 517 | 3 |  | Director-style video prompt composition for Muse. |
| `video_publish_package` | 903 | 3 | ✓ | Build account-safe manual publishing packs from validated Muse video products. |
| `video_qa` | 424 | 3 | ✓ | Fail-closed technical quality gate for locally rendered MP4 files. |
| `video_task_worker` | 1183 | 3 | ✓ | Bounded executor for durable Creator Initiative video attempts. |
| `video_visual_quality` | 202 | 3 |  | Non-semantic, local visual diagnostics for generated video. |
| `wan_video` | 646 | 3 |  | Local Wan 2.1 text-to-video generation through a fixed ComfyUI workflow. |
| `cantonese_asr` | 335 | 2 |  | Pinned, local-only Cantonese ASR and audio-timed subtitle generation. |
| `media_search` | 300 | 2 |  | Bounded image discovery and download for Muse's Phase 3 media studio. |
| `narration_evidence_contract` | 231 | 2 |  | Fail-closed, claim-level evidence contract for narration segments. |
| `narration_prosody_bridge` | 192 | 2 |  | Bridge structured narration into the fail-closed prosody contract. |
| `narration_sync` | 290 | 2 |  | SQLite-backed narration runs, timeline alignment and subtitle cue export. |
| `video_admission_runtime` | 486 | 2 |  | Production composition root for Commander-owned local video admission. |
| `video_commander_admission` | 532 | 2 |  | Commander-issued, one-shot admission grants for VideoStudio execution. |
| `video_face_framing` | 364 | 2 |  | Optional local Haar face framing for vertical highlight clips. |
| `video_visual_evidence` | 279 | 2 |  | — |
| `ffmpeg_skill` | 1125 | 1 | ✓ | Muse's local FFmpeg editing capability contract and probe-first diagnostics. |
| `media_compositor` | 348 | 1 |  | Narration-aligned media timeline composition for MuseSceneTimeline. |
| `narration_evidence_pipeline` | 102 | 1 |  | Orchestrate claim dossier lookup, narration evidence gating and run persistence. |
| `openfilm_adapter` | 258 | 1 |  | Export Muse shot/audio manifests into an editable OpenFilm project. |
| `prosody_contract` | 160 | 1 |  | Explicit, fail-closed prosody intent contract for segmented narration. |
| `remotion_motion` | 563 | 1 |  | Safe local Remotion rendering and FFmpeg assembly for Muse video products. |
| `scene_analysis` | 189 | 1 |  | Optional local OpenCV scene composition heuristics. |
| `smart_crop` | 118 | 1 |  | Focus-aware deterministic image cropping and Ken Burns parameter planning. |
| `video_audio_mix` | 183 | 1 |  | Safe, subtitle-timed background-music ducking helpers. |
| `video_checkpoint_adapter` | 565 | 1 |  | Fail-closed validation of historical VideoStudio checkpoint evidence. |
| `video_factory` | 535 | 1 | ✓ | One-command local video assembly from an asset folder using FFmpeg. |
| `video_factory_intake` | 939 | 1 |  | Topic/script/URL intake for Muse's Commander-owned video production pipeline. |
| `video_motion_diagnostics` | 101 | 1 | ✓ | Advisory optical-flow diagnostics for rendered video files. |
| `video_prompt_studio` | 171 | 1 | ✓ | Compose director prompts directly into a VideoStudio profile preview. |
| `video_recipe` | 212 | 1 |  | Safe, dependency-free adapters for external short-video parameter recipes. |
| `video_recipe_trace` | 372 | 1 | ✓ | Read-only provenance trace for MPT-derived Muse video recipes. |
| `video_reviewer_attestation` | 111 | 1 |  | Ed25519-backed, one-time video reviewer attestation verification. |
| `chimera_vocal` | 439 | 0 | ✓ | Optional local adapter for the MIT-licensed CHIMERA physical voice synthesizer. |
| `creator_media_plan` | 186 | 0 |  | Build rights-aware, narration-aligned media plans for Creator workflows. |
| `jyutping` | 104 | 0 |  | Strict tokenization and structural parsing for Cantonese Jyutping. |
| `kinetic_explainer` | 409 | 0 |  | — |
| `piper_tts` | 220 | 0 | ✓ | Offline Piper TTS adapter for Muse media workflows. |
| `prosodic_boundary` | 139 | 0 |  | Conservative gate for snapping narration cuts to reviewed phrase boundaries. |
| `video_editable_export` | 286 | 0 | ✓ | Export a verified Muse video product as an editable OpenFilm multitrack project. |
| `video_highlights` | 661 | 0 | ✓ | Local audio-first highlight selection and vertical clip rendering. |

### 4.7 界面 / API / Portal（6 个）

| 模块 | LOC | 被依赖 | CLI | 职责（模块 docstring） |
|---|---:|---:|:-:|---|
| `status_api` | 3343 | 2 | ✓ | Loopback-only status API with narrow authenticated Muse write routes. |
| `reporting` | 165 | 1 |  | Privacy-conscious half-hour operational reports for Muse. |
| `web_portal` | 1384 | 1 | ✓ | HTTPS-only server web portal with a constrained browser API gateway. |
| `local_dashboard` | 513 | 0 | ✓ | Authenticated LAN-capable browser host for Muse's local dashboard. |
| `portal_maintenance` | 202 | 0 | ✓ | — |
| `web_portal_admin` | 65 | 0 | ✓ | — |

> ⚠ `research_state_machine`（337 行）因语法错误未能被 AST 解析，未出现在上表；它属于“研究”组，职责是研究主张的状态机（evidence_collected → evaluate），被 `research_task_handler` 导入。

## 5. 模块间调用关系（核心骨架）

```
                         ┌──────────────── 用户入口 ────────────────┐
                         │ CLI (muse-*) · scripts\*.ps1 · Web Portal │
                         └───────┬───────────────┬──────────────────┘
                                 │               │
           灵感/研究入口         │               │  界面/API
 inspiration ──► initiative_engine ◄── status_api ◄── local_dashboard / web_portal
     ▲  (yijing/ziwei/qimen…)   │  ▲              │
     │                          │  │              ├─► product_builder / video_product_builder
 autonomous_research ───────────┘  │              └─► task_queue / research_knowledge / gpu_resources
     │  ├─► research_pipeline ─► research / research_fetch / scrapling_adapter
     │  ├─► research_knowledge（Research Wiki 图谱，被 14 个模块依赖）
     │  └─► product_builder（离线产品原型）
     │
 autonomous_runtime（本地监督器）
     ├─► worker ─► adapter_handlers / research_task_handler ⚠ / review_gate / improvement / evolution_executor
     ├─► video_task_worker ─► video_product_builder ─► video_factory / wan_video / cantonese_tts / video_qa / video_publish_package
     ├─► routines / watchdog / lease_manager / lease_recovery
     └─► task_queue（唯一 Ticket/审批/报告账本，被 34 个模块依赖）

 unified_runtime（Commander 入口）
     ├─► commander_planner / local_planner（DAG 规划，本地模型不可用则安全回退）
     ├─► orchestration（MultiAgentOrchestrator，受 resource_governor 限流）
     ├─► ruflo_adapter / desktop_adapter / mempalace_adapter
     └─► task_queue / memory / research_knowledge

 business_opportunities ─► initiative_engine / commander_planner / task_queue（商机 → 视频审批 → 入队）
 chatgpt_bridge / intake_runtime ─► task_queue（外部请求与事件准入，均先进入 waiting_approval）
```

**依赖中枢（被依赖次数最多）**：`task_queue`(34)、`research_knowledge`(23)、`initiative_engine`(19)、`progress`(16)、`model_client`(15)、`capabilities`(12)、`media`(12)、`memory`(11)、`model_call_context`(11)、`commander_planner`(10)。

**导入完整性**：除 `research_state_machine` 语法错误外，所有 `from muse.x import y` / 相对导入的目标模块与名称均存在（AST 逐一核对 src/tests/scripts/tools）。

**仅被测试或脚本使用的模块（无其他模块导入、无 CLI）**：celestial_calendar_provider、creator_media_plan、harness_evolution、jyutping、kinetic_explainer、lead_intake、lightpanda_adapter、mempalace_lifecycle、open_design_recovery、openrouter、ploid_adapter、product_feedback_import、prosodic_boundary、sandbox_file_manager、site_preview、skillify、social_content_kit、terminal_runtime、trace、usage_observer、zhenyi_bazi_provider。**完全无引用**：`interfaces`。

## 6. 端到端主流程（阶段 1：流程梳理）

> 保守假设（已记录）：Muse 不是“单输入→单输出”的程序，而是“请求 → 账本 → 审批 → 执行 → 产物 → 展示/反馈”的平台。结合项目目标（AI 辅助内容创作与视频变现），本次把下面三条链路定义为**主流程**；其中 B 是所有执行的公共骨架，A、C 是两条产出链路。

### 主流程 B：任务执行骨架（公共）
```
输入  : chatgpt_bridge submit / task_cli / intake_runtime / autonomous_runtime enqueue
  ↓   task_queue.TaskQueue（SQLite，状态 waiting_approval）
  ↓   人工审批：task_cli approve / muse_dev.py commander approve
  ↓   worker（租约+心跳+fencing）→ 按 kind 分发到 handler：
  │      adapter_handlers（媒体转码等）· research_task_handler ⚠ · evolution_executor · software_crafting · open_design …
  ↓   review_gate（独立审查）→ progress 事件 / artifact_evidence 校验
输出  : 任务结果 + 产物文件 + 进度事件 → status_api（/api/v1/*）→ local_dashboard / web_portal
```

### 主流程 A：灵感 → 离线产品
```
输入  : 目标文本（muse-inspire "…"）或 autonomous_research 空闲研究
  ↓   inspiration（记忆 + 象征引擎，输出“待验证假设”）
  ↓   research_pipeline → research_fetch → research_knowledge（Research Wiki 节点/边/证据）
  ↓   initiative_engine（Initiative 孵化、版本、审批）
  ↓   product_builder（自包含 HTML/ZIP 离线产品）
输出  : artifacts/products/... → status_api 产品库 → Portal 预览/下载
反馈  : scripts/import_product_feedback.py → product_feedback_import → Research Wiki（tested_by 回链）
```

### 主流程 C：选题 → 视频交付包
```
输入  : business_opportunities add/promote，或 video_factory_intake / 本地素材目录
  ↓   request-video-approval → Commander 审批 + video_commander_admission（AI 视频强制审批，fail-closed）
  ↓   video_task_worker（受 gpu_resources 租约约束）
  ↓   video_product_builder → video_factory / wan_video(ComfyUI) / cantonese_tts / remotion_motion / video_audio_mix
  ↓   video_qa（FFprobe/解码/响度/黑帧）→ video_publish_package（MP4 + 工程 ZIP + manifest + QA 报告）
输出  : 版本化视频产品 → status_api / Portal；自动发布与收益回流 = 规划中（未实现，不属于本次修复范围）
```

### 6.1 断点清单（流程视角）

| # | 位置 | 类型 | 影响 |
|---|---|---|---|
| B1 | `research_state_machine.py:180`（及 `:185`） | 语法错误（换行丢失，两条语句被拼到一行） | 模块无法导入 → `research_task_handler` 无法导入 → Worker 执行**研究类任务**时在延迟导入处抛 SyntaxError；主流程 A 的“研究”环节与 2 个测试模块中断 |
| B2 | `ember-fire/package.py:5` | 语法错误（同类换行丢失） | 《火种不灭》工程打包脚本无法运行 |
| B3 | 主流程 C 依赖 GPU/ComfyUI/Wan、粤语 TTS 模型、本地 LLM | 外部环境依赖 | 非代码断点；冒烟测试必须走“本地素材 + FFmpeg”与确定性 handler 路径 |
| B4 | 自动发布 / OAuth / 收益复盘 | 未实现（README 标注“规划中”） | 流程终点是“交付包”，不是“已发布”；本次不实现，只记录 |
| B5 | `prototype_builder`（有 CLI，无任何模块调用） vs `product_builder` | 重复职责 | 两套原型生成入口，易混淆 |
| B6 | `interfaces` 模块 | 死代码（零引用） | 无运行影响 |
| B7 | `chatgpt_bridge`、`intake_runtime` | 仅 CLI 入口，无模块调用 | 设计如此（外部入口），非断点 |
| B8 | README 中 `py -3.11 -m muse.research-wiki-audit` | 命令错误（模块名含连字符无法 `-m` 运行） | 按文档操作会直接报错；正确为 `-m muse.research_wiki_audit` 或 `muse-research-wiki-audit` |

**空实现 / TODO 检查**：全仓 `src/muse` 无 `TODO/FIXME/XXX` 注释；仅 3 处 `NotImplementedError`（`herdr_transport.py:249/251` 为能力不足时的显式拒绝，`skill_fusion.py:212` 为异常捕获），均不是空实现。

### 6.2 验证环境限制（重要）

本助手可用的代码执行环境**禁止创建子进程、禁止修改 `sys.path`、禁止导入 `importlib`**，因此无法在本机直接运行 `py -m unittest` 或启动 Muse 服务。阶段 4 的实际运行需要由用户在本机执行一条命令（脚本会把结果写入日志文件），再由本助手读取日志、定位并修复。详见 PROGRESS.md。
