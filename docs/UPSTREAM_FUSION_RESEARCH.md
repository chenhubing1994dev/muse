# Upstream Fusion Research: Super Agent Party + dsh-superagent

Research date: 2026-11-02

## Executive decision

Use DeepSeek dsh-superagent as the primary reference for Muse's autonomous work lifecycle, and treat Super Agent Party (SAP) as an optional external desktop/IM/MCP cockpit.

Do not import either complete application into Muse:

- dsh-superagent has the strongest reusable domain model: Source → Trigger → Eval → Decision → Mandate → Ticket → Assignment → Executor → Channel. Its MIT license permits code reuse with the notice preserved, but the repository explicitly calls itself a research prototype, not an official DeepSeek product or production-ready software.
- SAP has a broad integration surface (MCP, Skills, A2A, OpenAI-compatible API, browser/computer control, IM bots, extension system and multi-role chat), but its project license is AGPL-3.0. Copying or creating a derivative of its code can impose source-disclosure obligations on a distributed combined work. Keep it as a separately installed optional service and integrate through documented local APIs only if the user chooses.
- Muse already has SQLite TaskQueue/Worker, approval, idempotency, heartbeat/recovery, reminders, routines, progress events, improvement proposals and provider-neutral multi-agent fan-out. Replacing these with another task centre would duplicate working foundations.

## Repositories inspected

| Project | Snapshot | License | Decision |
|---|---|---|---|
| deepseek-ai/dsh-superagent-prototype | 6337fc823ce20251c8de8085948165c81aaa701e (main, package 0.1.0) | MIT | Reuse architecture; selectively port small code only when it materially improves Muse and retain MIT notice |
| heshengtao/super-agent-party | 3aac964122415fe289a1b8af1000474b397c916b (main, v0.4.3) | AGPL-3.0 | Do not copy source into Muse; consider separate-process API/MCP integration |

The repositories were inspected read-only in a temporary research directory. No package was installed, no service was started, and no third-party code was copied into Muse.

## 1. DeepSeek dsh-superagent: what is worth absorbing

### Strongest source files

- src/core/engine.ts: the domain's single writer. Intake, evaluation, decisions, ticket lifecycle and assignments are changed through synchronous commands and repository transactions.
- src/core/ports.ts: narrow interfaces for Policy, SourceScope, Assessor, Executor, Outbox and durable repositories. The core does not need to know which model provider, source or worker implements a port.
- src/policy.ts: deterministic admission gate, explicit versus inferred request strength, versioned policy, enabled-rule matching and mandate revalidation.
- src/app/effects.ts: durable SQLite side-effect queue. It coalesces work by (kind, key), persists before dispatch, uses generation to prevent a running attempt from deleting a newer replacement, applies concurrency limits and exponential backoff, and drains gracefully on shutdown.
- src/app/outbox.ts: one writer for outbound receipts; messages are idempotent, ordered per ticket/conversation, retried when temporarily unavailable and settled when the destination is permanently gone or ignored.
- src/app/runtime.ts: composition root, state-directory lock, startup recovery and graceful shutdown; it keeps domain rules out of the runtime wiring.
- src/executor/dsh.ts: one executor session and directory per Ticket, bounded worker slots, structured reports and restart recovery.
- Tests worth studying: tests/effects.spec.ts, tests/outbox.spec.ts, tests/policy.spec.ts, tests/runtime-concurrency.spec.ts, tests/store.spec.ts.

### Design ideas to port

1. Separate admission from assessment. The deterministic gate first checks source, identity, scope and whether the request is explicitly addressed. Only eligible inferred events reach a model assessor.
2. Default to observe. off ignores inferred work; observe records what would have been accepted without executing it; auto accepts only a positive assessment that matched at least one enabled rule. Explicit requests remain distinct from inferred work.
3. Make authorization durable. An immutable Mandate records source identity, policy version, strength, scope and matched rules. A later policy change must not silently widen old work; stale candidates become unavailable and resumed work is revalidated.
4. Give each work item a lifecycle. Ticket is the durable unit of authorized work; Assignment is one attempt/worker binding. A Ticket may be retried, resumed or handed to another worker without losing its authorization history.
5. Separate domain state from side effects. Commit the decision first; queue execution and outbound messages after commit. Use idempotency keys, generation/fencing and durable receipts.
6. Use structured reports. progress, waiting, ready, blocked, failed and dropped are better than inferring state from free-form text. A question for a person is a structured attention event.
7. Spend human attention carefully. Confirm once, stay quiet during routine work, and notify only when a result is ready, a decision is required or work cannot continue.

### Risks and limitations

- The repository explicitly warns that it is a research prototype, not production-ready, and differs from its internal system and the published design direction.
- It is a DeepSeek Harness bundle, not a standalone Python library. The inspected package expects Node.js ^22.19.0 or >=24, pnpm 11.7, a compatible DSH runtime (^0.2.1-alpha.2), and many @deepseek-ai/dsh-* peer packages. Its setup guide also expects Git, tmux, Python, GitHub CLI and optional Feishu CLI.
- Its execution guide says workers run shell commands as the operator's OS user without per-command approval. Command wrappers and per-ticket directories are defaults, not a security boundary; absolute executable paths can bypass wrappers. Muse must not copy this execution trust model. A real sandbox, resource/time limits, network policy and separate approval gates remain prerequisites.
- The prototype's default proactive mode is observe, which is the correct safety default for Muse.

## 2. Super Agent Party: what is worth learning

### Capabilities and source paths inspected

- py/task_center.py: JSON-file task records, progress/status updates, listing and cancellation.
- py/task_tools.py: model-facing create/query/cancel/finish task tools.
- py/sub_agent.py: iterative sub-agent loop, cancellation polling, progress reporting and bounded HTTP request timeout.
- py/mcp_clients.py: stdio / WebSocket / SSE / Streamable HTTP MCP connection management, tool discovery, heartbeat and reconnect loop.
- py/skills.py: GitHub ZIP skill download, extraction, discovery and installation.
- py/extensions.py, main.js: extension and desktop-host integration.
- README documents OpenAI-compatible /v1 and local MCP /mcp interfaces, plus IM bots, browser/computer control, agent skills, A2A and extension support.

### What to borrow conceptually

- The MCP connector surface is useful as an optional integration point: a separate SAP instance can expose its tools to Muse without embedding its code.
- Its extension ecosystem and Skill authoring workflow are useful references for a future Muse capability catalog and plugin UX.
- Its multi-role chat is a useful UI idea; Muse already has a provider-neutral MultiAgentOrchestrator, so role definitions and moderator/reviewer flows should be built on that core rather than importing SAP's task runtime.
- Its MCP client lifecycle suggests health probes, connection-state reporting and bounded reconnect/backoff for future Muse connectors.

### What not to copy

- TaskCenter stores one JSON file per task and uses an asyncio.Lock local to one process. That is weaker than Muse's existing SQLite transactions, idempotency keys, leases and cross-process recovery. It also uses short eight-character task IDs.
- SubAgentExecutor launches background tasks with asyncio.create_task, loops up to a configurable iteration cap and checks cancellation between model calls. It is useful as a UX/progress reference, but it is not a replacement for Muse's durable Worker and lease model.
- McpClient.call_tool can return None when disconnected or a formatted string on error instead of a typed failure result. The connection monitor uses a fixed heartbeat/retry loop; do not port it without typed errors, explicit lifecycle state and tests.
- The inspected GitHub skill installer downloads the selected branch ZIP and installs discovered skill folders. The path did not show commit pinning or archive hash/signature verification. Never auto-install third-party skills from a model suggestion; require a pinned revision, license and content/security review.
- SAP's dependency set is large and includes many model, media, IM, browser and platform integrations. Its pyproject.toml targets Python >=3.12,<3.13, while Muse intentionally keeps its core dependency-free and supports Python >=3.11. Do not import its runtime or dependency tree.

## 3. Muse versus the upstreams

Muse already has:

- TaskQueue: SQLite/WAL, unique idempotency keys, priorities, explicit approvals, cancellation, leases, heartbeat, bounded recovery and manual recovery for non-idempotent work.
- Worker: explicit handler registry, lease renewal, stale-worker result rejection, bounded loop-error backoff and structured progress events.
- MultiAgentOrchestrator: provider-neutral specialist callables, bounded concurrency (1–10), SQLite audit and partial-failure isolation.
- RoutineStore, ReminderStore, Watchdog, AutonomyPlanStore, ImprovementStore, CapabilityRegistry and ProgressEventStore.

The main gap is not another task queue. It is the event-to-authorized-work pipeline: source events, deterministic admission, model assessment, immutable authorization, Ticket lifecycle and reliable outbound delivery.

## 4. Fusion architecture for Muse

Source adapters (MCP / GitHub / Feishu / local events)
  → Trigger inbox + dedupe
  → Deterministic Policy Gate (source / actor / scope / addressed)
  → Assessor (structured rules; off / observe / auto)
  → Decision + immutable Mandate
  → Ticket / Assignment lifecycle
  → Existing TaskQueue + Worker (registered handlers only)
  → Structured Report / Progress
  → Durable Outbox / Reminder / MCP

Rules for this fusion:

- Keep SQLite TaskQueue and Worker as the execution engine. Do not create a second task centre.
- Keep MultiAgentOrchestrator as the role execution primitive; add durable Ticket coordination around it rather than another parallel orchestration stack.
- Start every inferred/proactive source in observe; do not enable auto until decisions have been reviewed and the source policy is explicit.
- Never let model-generated prose become a shell command or permission. A model can propose a Decision; the policy gate validates it; a Mandate bounds it; TaskQueue approval still gates risky handlers.
- Use a durable outbox for external replies and notifications. Do not send messages inside the same transaction that changes domain state.
- Treat every incoming source payload as untrusted evidence, not instructions. Keep private payloads local and pass only bounded, relevant context to a configured local assessor by default.
- Keep execution isolation separate from orchestration. A per-ticket directory is organization, not a sandbox.

## 5. First fusion slice implemented in Muse

Muse's intake pipeline now combines a local fixture adapter with an explicitly invoked, read-only public GitHub Issues adapter:

- `src/muse/intake.py` persists Trigger, Decision, immutable Mandate and an `intake_outbox` row. Mandate and Outbox are committed in the same SQLite transaction.
- `src/muse/intake_runtime.py` defines `SourceAdapter` and `RuleAssessor` protocols, an in-memory `FixtureSourceAdapter`, a bounded deterministic `KeywordAssessor`, and `GitHubPublicIssuesAdapter`.
- GitHub access happens only when the operator explicitly passes `--github-repo OWNER/REPO`; the adapter performs one HTTPS GET against the official API, reads public open issues only, skips pull requests, caps one page at 100, applies a 10-second timeout and 512 KiB response cap, and sends no token or Authorization header. No webhook, private repository, IM connector or background polling is configured.
- Fixture batches are capped at 100 events; policy/fixture files are size-limited and schema-checked. Assessor failures leave candidates retryable; retries use the canonical stored payload, not a replay's altered body.
- Durable Outbox delivery uses the existing TaskQueue idempotency key. If dispatch succeeds but outbox acknowledgement fails, replay returns the same task. Concurrent dispatchers converge on one task.
- Every dispatched `trigger_work_proposal` uses manual recovery and is in the Worker's default approval-required set. After explicit approval, the handler only writes a bounded local proposal packet; it never executes instructions from source content.
- The example policy defaults to `observe`. `auto` only allows a matched event to create an approval-gated proposal; it is not permission for unattended execution.

This is a one-shot public issue poll, not a continuous internet monitor. A real webhook, private repository support, model-based Assessor, Ticket/Assignment history and general-purpose execution sandbox remain future work.
1. Add a signed GitHub webhook or bounded polling routine only after explicit repository scope, event signature verification, rate limits and retry policy are configured.2. Add an optional bounded model Assessor behind the existing RuleAssessor protocol, with strict structured output, timeout, privacy boundary and policy-version revalidation.
3. Add Ticket/Assignment history and structured report states over the existing TaskQueue; keep Mandate scope immutable and approvals separate.
4. Add Assignment history and structured report states; support resume/retry without widening the Mandate.
5. Add a durable Outbox with per-channel receipts, idempotency, ordering, retry/backoff and graceful drain.
6. Add role profiles (researcher, planner, builder, tester, independent reviewer) over the existing MultiAgentOrchestrator; proposer and reviewer must be separate roles.
7. Add sandboxed execution only after filesystem/network/resource/time boundaries are enforced and independently tested.
8. If SAP is later installed separately, connect through its local MCP/API with explicit loopback binding and authentication. Do not bundle its AGPL code into Muse.

## Sources

- https://github.com/deepseek-ai/dsh-superagent-prototype
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/000-philosophy.md
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/001-domain.md
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/002-authorization.md
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/003-execution.md
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/007-architecture.md
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/LICENSE
- https://github.com/heshengtao/super-agent-party
- https://github.com/heshengtao/super-agent-party/blob/main/AGENTS.md
- https://github.com/heshengtao/super-agent-party/blob/main/py/task_center.py
- https://github.com/heshengtao/super-agent-party/blob/main/py/sub_agent.py
- https://github.com/heshengtao/super-agent-party/blob/main/py/mcp_clients.py
- https://github.com/heshengtao/super-agent-party/blob/main/py/skills.py
- https://github.com/heshengtao/super-agent-party/blob/main/LICENSE

## 6. Memory fusion: Tencent WeKnora + EverMind EverOS (2026-11-02)

### Upstream findings and license

- **EverOS** (`EverMind-AI/EverOS`, Apache-2.0): Python local-first memory runtime. Its core separation is Markdown as canonical human-owned memory, SQLite for queues/state/audit, and rebuildable vector/BM25 indexes. It separates user and agent memory tracks, extracts episodes/facts/profiles/skills from conversations and agent traces, and runs offline Reflection to merge related episodes, re-extract atomic facts and archive replaced fragments. Relevant code/docs areas include `src/everos/`, `core/persistence/markdown/`, `infra/persistence/markdown/writers/`, `docs/storage_layout.md`, `docs/how-memory-works.md`, `docs/reflection.md`. Do not import the full LanceDB/OME stack before measuring Muse's needs.
- **Tencent WeKnora** (`Tencent/WeKnora`, MIT): enterprise knowledge platform with RAG + Agent + Wiki on shared knowledge bases. Its memory service separates extraction, recall, topic promotion, document affinity and consolidation (`internal/application/service/memory/`); inferred memories remain pending until user confirmation, and memory kinds include profile/preference/fact/task/interest. The wider knowledge pipeline provides hybrid keyword/vector retrieval, RRF, reranking, parent-child chunks, source citations and revision/rollback for wiki/chunks. Reuse design concepts, not the Go/Vue/docreader application stack.
- License decision: both repositories are permissive (EverOS Apache-2.0; WeKnora MIT). Any copied source still requires preserving the respective license/NOTICE and attribution. This Muse slice reuses architectural ideas only; no upstream source code or dependency was copied/installed.

### Muse's chosen memory architecture

- Keep the existing SQLite `MemoryStore` as the transactional authority for now. The existing Obsidian Vault remains a safe, one-way human-readable mirror; changing it to Markdown source-of-truth would require a separately designed import/reconciliation migration and explicit approval, not an implicit flip.
- Add WeKnora/EverOS-inspired typed records: `profile`, `preference`, `fact`, `task`, `interest`, `episode`, `atomic_fact`, `foresight`, `skill`, `case`; and separate `user` vs `agent` tracks. Existing rows migrate to `fact + user` without data rewrite; pending candidate approval carries the selected kind/track into the active record; history records kind/track per revision; search/list can filter by kind and track.
- Agent Track is not automatically injected into personal user context. User Track and Agent Track remain independently retrievable; only confirmed/approved records are searchable.
- Obsidian notes include kind/track in frontmatter. Existing files remain create-only; no overwrite, deletion or reverse sync.

### Next high-value slices

1. Add an interest/topic signal table and threshold-based promotion to long-term interests, with a preview/approval step (WeKnora topic promotion pattern).
2. Add non-destructive consolidation proposals: cluster candidate episodes, show source lineage and before/after diff, and require approval before any merge/archive (EverOS Reflection pattern). Never run lossy consolidation repeatedly without review.
3. Add FTS5/BM25-style lexical retrieval and evaluation first; add vector embeddings/reranking only behind optional provider interfaces after local performance and privacy tests.
4. Persist Markdown export hashes and implement explicit diff/merge/rollback before considering Markdown as a writable canonical store.

### Sources

- https://github.com/EverMind-AI/EverOS
- https://github.com/EverMind-AI/EverOS/blob/main/docs/overview.md
- https://github.com/EverMind-AI/EverOS/blob/main/docs/storage_layout.md
- https://github.com/EverMind-AI/EverOS/blob/main/docs/how-memory-works.md
- https://github.com/EverMind-AI/EverOS/blob/main/docs/reflection.md
- https://github.com/EverMind-AI/EverOS/blob/main/LICENSE
- https://github.com/Tencent/WeKnora
- https://github.com/Tencent/WeKnora/blob/main/website-docs/03-features/23-memory.md
- https://github.com/Tencent/WeKnora/blob/main/docs/api/memory.md
- https://github.com/Tencent/WeKnora/blob/main/LICENSE

## 7. Swarm fusion: ruvnet/ruflo (2026-11-02)

### Upstream decision

- Ruflo (`ruvnet/ruflo`, MIT) is a broad multi-agent orchestration framework. The valuable patterns for Muse are hierarchical/swarm coordination, dependency-aware task execution, agent communication, lifecycle hooks, independent validation, bounded background workers, learning from execution traces, and federated nodes.
- Do not install or embed the full framework by default. Muse already owns its SQLite task queue, approval gates, lease/heartbeat recovery, routines, progress events, local memory and provider-neutral orchestration. A wholesale replacement would duplicate control planes and enlarge the dependency/security surface.
- Adopt the architecture incrementally and keep provider adapters optional. No Ruflo source code or dependencies were copied/installed in this slice.

### Muse implementation slice: dependency DAG + durable handoff log

- Extended `AgentSpec` with `depends_on` while preserving the existing fan-out `run()` API.
- Added `MultiAgentOrchestrator.run_dag()`: validates unique IDs, dependency references, duplicate edges and acyclicity before creating a run; executes ready DAG waves concurrently within the 1..32 hard worker bound, with the default ResourcePolicy recalculating CPU/RAM capacity before each wave; injects successful upstream outputs into downstream prompts as explicitly untrusted task data; blocks dependents when a required predecessor fails. Explicit `resource_aware=False` is reserved for deterministic stress tests.
- Added SQLite `agent_messages` audit records for started/completed/failed/blocked events and handoff content. This is a per-run durable handoff/audit log, not yet a general cross-agent messaging service.

### Muse implementation slice: Agent role/capability admission

- Added SQLite `AgentRoleRegistry` policies with role name, explicit allowed-capability list, descriptive trust tier and update timestamp.
- Added `AgentSpec.requested_capabilities`; both `run()` and `run_dag()` validate registered roles and deny capabilities outside the role allowlist before a durable run is created.
- A declared capability requires a supplied role registry. Unknown roles and over-broad capability declarations fail closed.
- Security boundary: this is admission control over declared metadata, not a sandbox for arbitrary Python callables. `trust_tier` is currently audit metadata only. Actual tool execution still needs a controlled Tool Gateway that checks the same policy at invocation time, with approval gates for sensitive operations.
- Validation: full Python 3.11 suite passed (266 tests); focused orchestration suite passed (13 tests) under Python 3.11 and Python 3.14; both Python versions passed compileall. The workspace is non-Git, so no Git-based diff/freshness review is available.

### Remaining high-value Ruflo patterns

1. Controlled Tool Gateway: connect role allowlists to CapabilityRegistry readiness and invocation-time authorization.
2. Independent Reviewer that cannot approve its own author's change; structured acceptance and regression evidence.
3. Event Hooks wired to existing ProgressEvent/TaskQueue/Watchdog rather than another scheduler.
4. Durable DAG checkpoint/recovery, bounded retries, cancellation and timeout propagation.
5. Learning Loop that converts reviewed successful traces into pending Agent Track skill/case proposals.
6. Federation only after node identity, mutual authentication, trust scopes, data minimization and revocation exist.
### Sources

- https://github.com/ruvnet/ruflo
- https://github.com/ruvnet/ruflo/blob/main/README.md
- https://github.com/ruvnet/ruflo/blob/main/LICENSE

## 8. REA + Craft Anything + ego-lite：软件研究、公共网页研究与创造引擎（2026-11-12）

### REA — Reverse Engineer Anything

- Official repository: https://github.com/morluto/rea
- 本机 npm 包 `rea-agents` 版本 6.5.0；Node.js 24.20.0 / Windows x64。REA 目录列出 207 个 MCP tools；只读 `list_cutter_sessions` 已通过 Muse 的既有 `MCPStdioClient` 实际调用。
- `analyze_javascript_application` 的 MCP 调用未返回 JSON-RPC tool result。Muse 为这个长分析工具使用 REA 官方 CLI 命令 `analyze-javascript-application`，不伪称 MCP 调用成功。
- 真实自有目标分析：`D:\AI AGENT\Muse\ui-prototype`，只选择 `package.json` 与 `dist/index.html`。证据：`data/software_crafts/b100-ui-prototype/rea-js-summary-5/rea-evidence.json`（36,102,929 bytes），SHA-256 `75893b1f0f25a3cde6e0bcd2736e3c5c30b8f0bff0cbd7f96f6a64a2bb8d28c9`；旁边的 `evidence-manifest.json` 保存目标、工具、字节数、哈希与采集时间。
- 本次输出包含 2,754 graph nodes、2,809 edges、1,252 semantic nodes；1,402 个未选文件只列入 inventory，并不代表已逐一分析。没有把目录清单或静态分析结果包装成功能等价证明。
- `rea doctor` 仍报告 Ghidra/IDA/JADX 未配置、Hopper 不支持当前 Windows，以及若干客户端注册缺失/漂移；这些可选 Provider 不标记为 ready。不要未经 Node 兼容性检查升级已安装 REA。

### Craft Anything

- Official repository: https://github.com/storytold/craft-anything
- 官方工作流 Study → Build → Check → Ready；Clean-room 是重建策略，不是 OS sandbox。MIT / Apache-2.0 二选一，目标软件和第三方素材仍需单独授权。
- 已安装 Windows x64 CLI：`D:\Tools\CraftAnything\0.6.1\cli\craft-anything-cli.exe`。官方 portable ZIP 为 69,934,191 bytes；SHA-256 `BD370FBAA2B44DBC13581DB5FFD2BF66924ED40E433C7902212AA17CA781DE54` 与 GitHub release digest 完全一致，ZIP CRC 测试通过。
- 安装 CLI SHA-256：`21E01CCD80E3CC3024101DD1F9DF56180CDF269868F6024373763845A1B2E086`；Windows Authenticode = Valid，Signer = Learning Machines Inc.。Craft doctor 报告 79 MCP tools、20 read-only；`core_session_show` 实际调用成功。
- Muse 的 `config/muse.toml` 使用 `[models]` 表；旧版 `configure_local_backend()` 只读 `[model]` 导致 setup 失败。现已兼容 `[models]` 与旧 `[model]`；`muse-software-craft setup` 实测成功，将 `Muse Local` 设为 builder，`llama_cpp` / `DeepSeek-V4-Pro-Qwen3.5-9B` / `http://127.0.0.1:1235/v1` connectivity test passed（11,280 ms，reply=`ready`）。
- 对 Muse 自有本地 UI 测试页调用 `web_site_open` 返回 `consent_required`。这是 Craft 自己的 consent gate；Muse 不绕过，不启用 `--allow-run`。Craft website Study、Build、Check 仍需在合规授权后完成真实纵向验收。

### ego-lite — 公共浏览器研究与素材观察

- Official repository: https://github.com/citrolabs/ego-lite；MIT。源码固定于 v2.2.0 tag / commit `bacaece43900e8ca68eea3b6f1663835ea38df9e`，工作树干净。
- Windows 桌面 app 已通过官方 Microsoft WinGet 包 `CitroLabs.EgoLite` v0.6.2.10 安装；官方安装器 SHA-256 `C4B4DC2D01E0F2F11C27249283BC2D505ACD483D143F5687644274062F658786`、Authenticode Valid（CITRO LABS PTE. LIMITED）。实际安装在 `C:\Program Files\Citro Labs\ego lite\Application`（system-level）。Runner 的 `Start-Process` 首次启动尝试返回 PID 后进程立即退出；没有存活的 ego 进程或可确认的 GUI onboarding，`ego.exe` Authenticode Valid。`ego-browser` CLI 尚未注册到 PATH；Muse adapter 已能从标准安装根目录发现绝对 CLI 路径，Doctor 实测 `installed_unverified` / `cli_on_path=false`，runtime probe 仍未运行；没有导入 Chrome 数据或登录态。需在交互式 Windows 桌面完成首次启动/onboarding，真实浏览器运行仍未验收。
- `muse.ego_browser.EgoBrowserAdapter` 提供 runtime probe、Google 公共搜索、动态 DOM 文本/链接/媒体清单、页面截图与来源元数据、同源图片/PDF 下载，以及 JSON provenance manifest。
- 素材下载必须有明确 URL 与 hostname allowlist；HEAD 预检 Content-Length / Content-Type，手动重定向（不跟随 3xx）、尊重 CORS；只收集 image/* 与 PDF，检查实际大小并计算 SHA-256。每项标记 `license_status=unreviewed`、`reuse_allowed=false`、`rights_review_required=true`。
- `muse.browser_research.BrowserAugmentedSearchAdapter` 只把当前研究课题的 curated support/counter query 送往 Google 公共搜索，不把私人记忆或任意聊天文本直接发送；结果仅作发现线索，必须再读取来源和反证。
- `HybridSourceReader` 保留现有带 SSRF 防护的 HTTP reader 为主路径；只有页面过短、动态内容不可读或返回挑战页时，才尝试 ego-lite 公共页面 fallback。只允许公开页面；不登录、不填写表单、不读取私人账号内容、不绕过 CAPTCHA/paywall/访问控制。
- 在浏览器 fallback 页面上，最多自动收集 3 个同源图片/PDF，每个 2 MiB、总计 5 MiB；只用于内部研究，权利状态未核验，不允许自动发布/转售。视频、音频、第三方 CDN 与整站批量抓取不在当前自动收集范围。

### Muse 融合职责与边界

- REA：已授权目标的静态/结构分析与证据记录。
- ego-lite：公共搜索、动态网页观察、截图、有限同源素材采集；作为现有 ResearchPipeline 的可选执行层。
- Craft Anything：已授权软件 Study / Compare / Check 与后续建构能力；任何 provider consent 仍必须遵守。
- Muse Commander：唯一调度中心；复用既有 TaskQueue、Worker、Approval、TestRunner、ReviewGate、ProductBuilder。REA / ego-lite / Craft 都是能力 Provider，不创建第二个 Commander。
- 计划生成仍由 `muse.software_recreation.build_recreation_plan` 提供；真正入队使用现有 `SoftwareCraftingAdapter.enqueue_task`。临时重复的 `MCPStdioSession` 与未被 Worker 消费的 `enqueue_recreation_plan` helper 已移除。
- 当前状态：REA 对自有 UI 原型的真实静态分析通过；Craft CLI/签名/核心 MCP 通过但 website Study 被 consent gate 阻挡；ego-lite v0.6.2.10 已安装并验签，adapter 可发现绝对 CLI，但 onboarding/runtime probe 未完成；Build→Check→Independent Review→Product Package 尚未完成。

### 禁止事项

- 不创建第二个 Commander，不让外部 Builder 绕过 Muse TaskQueue。
- 不把 MCP tool listing、doctor 通过、网页截图或搜索 snippet 当作真实目标功能分析成功。
- 不访问未授权私有页面，不导出浏览器 Cookie/密码，不绕过登录、CAPTCHA、paywall、DRM、反作弊或其他访问控制。
- 不把未核验许可证的素材当作可自由复用，不自动公开、转售或发布下载资产。
- 不承诺任意软件 100% 完美复刻；缺失观察不等于不存在或通过。