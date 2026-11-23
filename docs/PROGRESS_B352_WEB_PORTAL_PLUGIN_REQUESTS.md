# B352 — Server Web Portal 插件能力请求闭环与 PWA 跨端图标

日期：2026-11-22

## 本轮落地

- HTTPS Web Portal 增加插件 Manifest 候选登记入口：POST /api/v1/portal/plugins/discover。仅接受 schema v1 manifest，登记后保持 discovered，响应明确标记需要审核且不执行。
- 已由本机管理员独立批准并启用的插件，可从网页提交能力请求：POST /api/v1/portal/plugins/{plugin_id}/capabilities/{capability_id}/request。请求通过现有 commander_task_queue_adapter 写入 Commander TaskQueue，状态为 waiting_approval。
- 网页端没有 approve/enable/disable 操作；未启用插件不能提交能力请求。即使插件已启用，CapabilityRegistry 条目仍是 needs_approval、executable=null、execution_enabled=false。
- 写入口要求 HTTPS、精确 Origin/Host、登录 Cookie session 与 CSRF；严格检查 Content-Type、单一十进制 Content-Length、请求体大小、重复 JSON key、非有限数和 JSON 对象形状。Manifest 本身仍受 64 KiB schema 限制。
- 浏览器表单只做 JSON 对象形状预检，但通过 rawBody 原样发送 JSON envelope，避免 JSON.parse/stringify 抹掉重复键，让服务端严格解析器仍能拒绝重复 key。
- Web PluginHub 增加候选登记表单及已启用能力请求表单；成功请求显示 Commander task ID。插件列表与网页卡片同步显示 unsigned / verified / revoked 签名状态及 execution_enabled=false 边界，页面不执行插件代码。
- PWA 增加无第三方依赖的 PNG 图标生成器，产出 iOS 180×180 Apple Touch Icon、Android/桌面 192×192 与 512×512 图标；manifest、HTML apple-touch-icon 与 Service Worker shell cache 同步更新。
- Web Portal 数据库路径要求插件、任务、能力数据库为不同的普通文件且不能是符号链接；静态根目录拒绝符号链接。CapabilityRegistry 在插件列表读取/候选登记时进行声明元数据同步；只有注册表计数稳定且本次列表覆盖全部插件时才清理旧能力，超过 500 条或快照变化时 fail-safe 跳过 stale sweep。
- 新增受保护的 `GET /api/v1/portal/tasks` Commander 任务账本；只返回最近 40 项任务的安全元数据，不暴露 payload、lease 或 Worker 内部字段；网页只读展示，不改变 Commander 调度权，接口故障时仅任务面板局部降级。
- Portal 管理员凭证现持久化为 Session SQLite 中独立随机盐 + scrypt 摘要，不保存明文；后续启动必须匹配存储凭证，否则 fail closed。新增无回显 `muse.web_portal_admin` 密码轮换命令，要求当前密码并撤销所有浏览器 Session。
## 验证结果

- Web Portal + Plugin Platform + Portal DB maintenance 专项回归：**40/40 passed**（76.309 秒）；覆盖 Portal 持久化管理员凭证、TLS/CSRF、插件 Manifest、Ed25519 信任根/签名包完整性/撤销、Commander 入队、灵感闭环、任务账本脱敏，以及 SQLite WAL 在线备份、SHA-256 校验、CLI 还原与拒绝覆盖。
- 最新全仓 Python 回归：**2,404 tests / 0 failures / 0 errors / 0 skipped**（339.125 秒）；`scripts/validate_full_suite.py` Job `wc_job_jYcIORrUNPTKV8qR` exit 0。修复全仓验证脚本漏加项目根路径导致的 8 个模块导入错误；受影响模块定向复测 74/74 PASS。此前唯一 skipped 的真太阳时上游比对单测现已 1/1 PASS，依赖来自既有隔离环境 `artifacts/ziwei-poc-venv`，未污染 Muse 主环境。前端 Node 历史验证：**66/66 passed**；`web-portal/app.js`、`sw.js` Node syntax check 通过。
- PNG 专项测试验证三个图标的签名、尺寸、每个 PNG chunk CRC 与 IDAT 解压长度。
- PWA 图标生成脚本经 Muse Dev / PSMUX 执行成功，三个 PNG 均已生成。

## 安全边界与未完成事项

- 插件发现、能力声明和网页请求不代表授予执行权限；当前仍无任意第三方插件 runtime。
- 已新增 Ed25519 发布者信任根与签名 bundle 验证：可固定/撤销发布者公钥、验证 canonical manifest + artifact SHA-256 的域分离签名、拒绝未知/撤销/发布者不匹配的密钥与被篡改包，并将签名记录绑定插件版本和 manifest digest。相同版本与 manifest 不允许替换不同 artifact；撤销密钥会使其已验证签名记录转为 revoked。Status API、Web Portal 与 PluginHub 卡片均可读取签名状态。
- 签名验证只做供应链身份/完整性检查：不解压、不执行、不启用插件，也不绕过人工审批。WASM/受限子进程沙箱、资源配额、运行时健康检查、版本回滚和 Commander Worker 正式执行适配器仍未完成。
- 安装签名验证依赖：`pip install -e '.[plugin-security]'`。管理员以 raw 32-byte Ed25519 公钥导入信任根：`muse-plugins trust-key <key-id> --publisher <publisher> --public-key-file <public-key.bin> --database <plugin-db> --trusted-by <operator>`；验证候选包：`muse-plugins discover-signed <manifest.json> <artifact-file> --key-id <key-id> --signature-file <signature.bin> --database <plugin-db>`（签名文件为 raw 64-byte Ed25519 signature）；撤销：`muse-plugins revoke-key <key-id> --database <plugin-db> --capability-db <capability-db> --revoked-by <operator>`；修复/重试能力目录同步：`muse-plugins sync-capabilities --database <plugin-db> --capability-db <capability-db>`。两个数据库必须是不同的现存普通文件；撤销与能力目录同步为顺序操作，不是跨库原子事务。签名者必须按 `plugin_signature_payload(manifest, artifact_sha256)` 生成签名；单个 artifact 上限 256 MiB。
- Android、iPhone、iPad、macOS、Windows 的真实设备/浏览器验收尚未执行；当前完成的是响应式网页与 PWA 资源适配，不宣称实机全兼容。
- 用户域名、TLS 证书、公网服务器部署和外网验收尚未执行；未发布公网。
- 改动写入当前绑定的 Muse 项目工作区；未部署公网，未执行项目迁移或合并操作。项目不是 Git 仓库，使用逐文件读取、版本校验与自动化测试验证。

## 2026-11-23 follow-up — Plugin risk ceilings and approval budgets

- Manifest validation now treats `risk` as a permission ceiling: `read_only` cannot request public network access, `research` cannot request project writes or GPU/video permissions, and only higher risk classes can request their corresponding permission families. This prevents a plugin from labelling a privileged action as low risk.
- Commander approval envelopes now include risk-derived proposed CPU, wall-time, memory, output, network, filesystem-write and GPU limits. They are explicitly tagged `runtime_enforced=false`; they are review metadata only, not an active sandbox or OS quota.
- Post-gate focused regression: Plugin Platform **27/27 passed** (1.180s), Web Portal **17/17 passed** (74.172s), Status API **49/49 passed** (27.357s).
- Commander capability admission requires a current `verified` Ed25519 bundle signature; unsigned or revoked bundles cannot create approval tickets, even if an administrator enabled the manifest. Portal and Status API return 409 for unsigned requests; no Commander task is enqueued.
- Commander approval envelopes persist publisher key ID, artifact SHA-256, signature digest and verification timestamp, so reviewers can trace which signed bundle requested the capability. This is provenance evidence, not a runtime grant.
- Runtime execution remains fail-closed: `execution_enabled=false`, `execution_adapter_required=true`. Proposed risk budgets are review metadata only (`runtime_enforced=false`); WASM/restricted-process sandbox and enforced resource quotas remain unimplemented.
- Latest full `scripts/validate_full_suite.py` regression after all changes: **2,420 tests / 0 failures / 0 errors**, 346.513s, Job `wc_job_TaBNPVFqNPJV73fv` exit 0. Python compile checks passed for all four changed implementation/test files; AST scan found **0 duplicate test methods**.

## 2026-11-23 follow-up — Commander approval timestamp compatibility

- 修复插件审批复核对 Commander 实际审批时间戳格式的误拒绝，并补回损坏的 payload 类型与 kind 检查；Plugin Platform **28/28 passed**。
- 最新完整回归：**2,421 tests / 0 failures / 0 errors**（346.510 秒），内层 psmux Job `884855b55a144a6aaa5843ee19da8037` exit 0。

## 2026-11-23 follow-up — Extreme approval timestamp rejection

- `validate_approved_plugin_ticket` now rejects extremely large numeric `approved_at` values as invalid approval metadata rather than leaking `OverflowError` from float conversion. New regression first reproduced the exception with `10**400`, then passed after the bounded conversion fix.
- Plugin + business opportunity + product/prototype/video creation suites: **119/119 passed**; final full suite: **2,422 tests / 0 failures / 0 errors** (342.666s), psmux Job `790b3a845c2d47ff89622a868073ee46` exit 0.
- TaskQueue decode and research-evolution statement formatting was cleaned without changing dispatch or approval semantics. Third-party execution remains disabled pending a verified sandbox runtime.

## 2026-11-23 follow-up — Shared approval-event timestamp binding

- Centralized UTC approval/event timestamp matching in `TaskQueue` and applied it to plugin, video-worker, business-video, code-change, MemPalace, and video-trace approval consumers.
- Cross-module approval regressions: **190/190 passed**; final full suite **2,427 tests / 0 failures / 0 errors** (342.297s), psmux Job `3c0f97debb4d4828babac6303f417c27` exit 0. Third-party plugin execution remains disabled.

## 2026-11-23 follow-up — Wasmtime release staging (verification pending)

- Staged the official Bytecode Alliance Wasmtime 51.0.0 Windows x86-64 zip under `.tools/wasmtime/staging`; downloaded bytes **12,560,257**, SHA-256 matches the official immutable release asset digest. Retrieved the release attestation bundle; its release/v0.2 subject includes the same asset digest.
- Full cryptographic release-asset verification is blocked because local GitHub CLI 2.105.0 is signed out. `gh release verify-asset v51.0.0 <zip> --repo bytecodealliance/wasmtime` is the correct check; no binary was extracted, installed or executed. Fail-closed plugin execution remains in effect.

## 2026-11-23 follow-up — Portal initial administrator credential helper

- Added `muse.web_portal_admin --generate-initial-password`, producing a 48-byte CSPRNG URL-safe (384-bit) secret; no session DB mutation and no plaintext persistence. TDD RED confirmed missing API; implementation GREEN.
- Deployment guide requires running only in a trusted interactive local terminal (not psmux/CI/log capture/redirection), storing in a password manager, and injecting via protected service environment. Portal remains explicitly single-admin; multi-user/RBAC is not claimed.
- Verification: Web Portal + Deployment **22/22 passed** (73.647s); Python full suite **2,434 tests / 0 failures / 0 errors** (343.187s), psmux Job `d2c7f9f7e8664cc2a8155666dc72a374` exit 0.

## 2026-11-23 follow-up — Inspiration evolution async Commander dispatch

- Replaced synchronous HTTP execution of `evolve_inspiration_closed_loop()` with an idempotent Commander TaskQueue Ticket (202 Accepted). `AutonomousSupervisor` now has a strict allowlisted handler and selects only this local workflow kind; Worker lease/heartbeat/failure lifecycle is reused.
- Portal submits a random request ID, reuses it on retry, and shows the Commander Ticket/status; duplicate replay remains stable after task completion, mismatched payload reuse is rejected. The runtime must be active; otherwise tasks remain queued. A process-table check found no `muse.autonomous_runtime` / `autonomous_runtime.py` process on the target Windows host; start from the project root with `py -3.11 -m muse.autonomous_runtime run`. No actual runtime/GPU job was started by this work.
- Four focused module groups: **66/66 passed** (85.935s), py_compile **6/6**, `node --check web-portal/app.js` passed. Final Python full suite: **2,437 tests / 0 failures / 0 errors** (344.600s), psmux Job `5f568173dffe48cba9c714790fa24804` exit 0.

## 2026-11-23 follow-up — Runtime state visibility in Portal

- `/api/v1/overview` now includes a privacy-bounded `autonomous_runtime` state projection (state/running/heartbeat age/stale only); Portal shows ONLINE, HEARTBEAT STALE, or STOPPED and explicitly warns that tickets wait while stopped.
- Status API + Portal + Deployment: **71/71 passed** (100.794s), py_compile **2/2**, Node syntax check passed; final Python suite **2,437 tests / 0 failures / 0 errors** (342.365s), psmux Job `ac48c2bc01444898a0627f138afccff3` exit 0. No runtime was started.

## 2026-11-23 follow-up — Portal runtime lifecycle controls

- Added CSRF-protected, allowlisted start/pause/resume/stop controls proxied to the existing Status API daemon controller; no `once` or arbitrary control path exposed. Stop requires a browser confirmation; buttons track runtime state.
- TLS integration test covers missing CSRF (403), invalid action (400/no upstream call), and all four valid lifecycle actions. Focused suite **71/71 passed** (113.405s), py_compile **3/3**, Node syntax check passed. Final full suite **2,437 tests / 0 failures / 0 errors** (355.795s), psmux Job `634be5100b0c41dba34fbdd6a4bc67d6` exit 0. Controls were not clicked; real runtime remains stopped.

## 2026-11-23 follow-up — Daemon start failure rollback

- A failed log open or child-process spawn no longer leaves a pre-marked runtime in a false `running` state. Rollback is a conditional SQLite update that only resets an unowned runtime; a concurrently acquired Supervisor lease is preserved. Partially opened log handles are closed.
- DaemonControl **10/10 passed**; cross-module Commander/Runtime/Status API/Portal/Deployment **108/108 passed**; py_compile **3/3**; Node syntax check passed. Final full suite **2,440 tests / 0 failures / 0 errors** (357.205s), psmux Job `f2277ee341454159879cc70b185bbcb6` exit 0. No production runtime was started.
