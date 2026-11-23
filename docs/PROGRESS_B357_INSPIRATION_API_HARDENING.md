# B357｜灵感演化 API 深层 JSON fail-closed 修复

日期：2026-11-23

## 问题与修复

`POST /api/v1/inspirations/evolve` 将请求体限制为 4 KiB，但深层嵌套 JSON 仍可在 `json.loads` 阶段触发 `RecursionError`。此前该异常未被路由捕获，导致 BaseHTTPRequestHandler 记录未处理异常并关闭连接，调用方收不到结构化 HTTP 响应。

现已为该路由增加专门的 `RecursionError` 分支，返回 HTTP 400 和固定错误信息 `request nesting exceeds the supported limit`。灵感演化路由和 Daemon lifecycle 路由也都拒绝负数 `Content-Length`，避免把非法长度当成空请求并意外触发演化或运行控制操作。

Portal SQLite 恢复现在要求显式提供 64 位小写十六进制 SHA-256，并使用常量时间比较核验备份文件；CLI 的 `restore` 命令必须传入 `--expected-sha256`。目标必须是全新路径，哈希不匹配时不会创建恢复目标。

## 回归证据

- 在 `tests/test_inspiration_evolution.py` 的真实 HTTP 测试中新增 1,100 层嵌套 JSON 请求（约 2.2 KiB），验证服务端返回 HTTP 400 和固定错误信息。
- 新增 Daemon 控制接口原始 HTTP 回归，发送 `Content-Length: -1`，验证返回 400 且控制器未被调用。
- Portal 备份恢复测试覆盖哈希不匹配拒绝、匹配后恢复、禁止覆盖、CLI 参数契约。
- 灵感演化 + Status API + Portal maintenance 定向联合回归：**64/64 passed**（33.568 秒）。
- B357 当时的全仓 Python 基线为 `scripts/validate_full_suite.py` **2,404 tests / 0 failures / 0 errors**（365.075 秒）；该历史结果已由 B362 最新全仓回归取代。

## 安全与未完成项

- 没有开启自动代码修改、插件任意执行、自动发布或公网部署。
- 外部研究仍由每条灵感的显式授权控制；象数体系仍仅作灵感/假设，不作为事实证据。
- 仍待完成：插件隔离执行 runtime 与资源 broker、Commander Worker 的正式授权 Grant 接线、Android/iOS/PC/Linux 实机验收，以及服务器域名/证书/公网部署前置条件。

## B358｜插件发布者撤销后的请求阻断

插件发布者签名密钥撤销后，已登记插件的签名状态会同步变为 `revoked`。此前 `commander_request` 仍可能接受该插件的新能力请求并送入 Commander 审批队列。现已在 Commander 请求入口重新检查当前签名状态；若签名已撤销则 fail closed，不调用 enqueue。未签名的本地开发声明流程保持原有行为，但仍只生成需要人工审批的 Commander 请求，且不执行插件代码。

- TDD：新增撤销密钥后不得再提交 Commander capability request 测试；先确认旧实现失败（`PluginError` 未抛出），再加入最小拒绝逻辑。
- `tests.test_plugin_platform`：**21/21 passed**（0.818 秒）。
- B358/B359 阶段的 **2,404 tests** 仅为旧基线；最终验证以 B362 的 **2,411 tests / 0 failures / 0 errors** 为准。
## B359｜发布者密钥撤销联动禁用插件

在 B358 阻断撤销密钥插件新请求的基础上，继续发现一个状态一致性问题：发布者密钥撤销后，插件虽然不能再提交请求，但数据库中的 `plugins.status` 仍可能保留 `enabled`，让控制台与能力目录呈现误导性的启用状态。

- 撤销密钥事务现在会将该密钥签署的全部已启用插件统一置为 `disabled`，同时把对应签名标记为 `revoked`，为每个受影响插件写入审计事件；全部在同一 SQLite 写事务中完成。
- TDD：新增测试先确认旧行为错误地保留 `enabled`，修复后通过。
- `tests.test_plugin_platform`：**22/22 passed**（0.834 秒），包括撤销后禁用状态与 Commander 请求拒绝。
- 插件执行仍固定关闭；该变更只改变信任/启用状态，不加载或执行插件代码。

## B360｜撤销状态与共享能力目录同步闭环

- 核查确认：`revoke_publisher_key` 在插件注册库事务内撤销签名并禁用受影响插件；共享 `CapabilityRegistry` 是独立数据库，不可能与插件注册库共享一个 SQLite 原子事务。
- `sync_plugin_capabilities` 现在同时检查插件管理状态与当前签名状态；即使插件仍处于 `discovered`，只要发布者密钥已撤销，其能力也会投影为 `disabled`，并在 metadata 保留 `signature_status=revoked`。能力仍固定 `executable=None`、`execution_enabled=false`。

## B361｜发布者撤销 CLI 与能力目录恢复命令

- `muse-plugins revoke-key` 现在要求同时指定现存插件数据库与能力数据库；执行前验证两者均为普通非符号链接文件且规范化路径不同。撤销插件库中的发布者密钥后，立即同步共享能力目录。
- 新增 `muse-plugins sync-capabilities --database <plugin-db> --capability-db <capability-db>`，可在跨库同步中断后幂等修复能力目录；同步使用现有完整快照与 stale-sweep 保护，不把两个数据库误称为原子提交。
- 若撤销已提交但能力目录同步失败，CLI 会明确提示密钥已撤销及恢复命令；不会回滚或伪报同步成功。Commander 请求入口仍独立检查签名撤销状态，能力执行继续关闭。
- TDD 先验证旧 CLI 缺少同步参数/命令，再发现并修复“已撤销但仍是 discovered 的候选能力没有被禁用”的投影缺口。
- Python 3.11：CLI 撤销/同步/恢复三项集成测试 **3/3 passed**；同步失败注入、明确报错及补偿恢复测试 **1/1 passed**（0.087 秒，psmux Job `d556b75bd81043e7a460ed2f376bdf0a`）。Plugin Platform + Web Portal + Web Portal Deployment 联合回归 **43/43 passed**（77.408 秒），psmux 内层 Job `a3d75707a1e2428a99fd3574ccd893d0` exit 0，WebCodex Job `wc_job_JUGlzJSlU04m4GX7` exit 0。
- 本轮没有执行插件解压、第三方代码运行、外部部署或发布；跨数据库同步仍是顺序提交，`atomic_across_databases=false` 明确保留。

## B362｜跨库撤销闭环最终全仓回归

- 最新全仓 Python 回归：`scripts/validate_full_suite.py`，**2,411 tests / 0 failures / 0 errors**，345.149 秒；WebCodex Job `wc_job_PKBtrdPb4RT7NoaP` exit 0。
- 回归包含 CLI 撤销成功、能力目录禁用、独立补偿同步，以及同步失败注入后确认密钥仍已撤销、能力目录可恢复的测试；完整 unittest 汇总 `Ran 2411 tests ... OK`。
- 测试运行器 PowerShell 对 Python 标准输出的转发曾出现 `NativeCommandError` 格式提示，但 Python 测试进程最终退出码为 0，完整 unittest 汇总为 `OK`，WebCodex 作业也以 exit 0 完成；该提示属于输出流包装，不是测试失败。
- B362 记录的是上一阶段跨库撤销闭环回归；后续 B363 继续补齐 Web Portal 可信代理来源边界。
## B363｜Web Portal 可信代理客户端来源与公网 URL 回报

- 新增可选 `--trusted-proxy-cidr`（最多 16 个）；默认不信任任何代理头。只有 TCP 直接对端命中显式 allowlist，且请求恰有一个 `X-Forwarded-For` 标头并包含单一合法 IP，才将该 IP 用于登录限速与审计来源。
- CIDR 严格解析并限制最宽 IPv4 `/16`、IPv6 `/48`，优先配置代理单 IP `/32` 或 `/128`。重复标头、逗号分隔链、非法 IP、未配置代理均回退到直接 TCP 对端，不会把无效输入当作可信客户端地址。
- `Forwarded`、`X-Real-IP`、`X-Forwarded-Proto`、`X-Forwarded-Host` 不参与 TLS、Host、Origin 或客户端来源证明。反向代理必须覆盖写入 XFF 为其观察到的远端 IP，并继续以 HTTPS 验证连接 Portal 上游。
- `--origin` 继续为必填的精确 HTTPS 公网 origin；启动 JSON 新增 `configured_public_url` 与实际生效的 `trusted_proxy_cidrs`，便于部署系统核对配置。此回报不代表已完成真实域名、证书、防火墙或公网验收。
- Web Portal + Deployment 专项回归 **20/20 passed**（76.156 秒）；其中包含代理 allowlist、单一 XFF、重复/链式/无效头回退，以及真实 HTTPS 登录审计来源地址验证。
- Portal 仍是单管理员凭证模型，不宣称多用户 SaaS、MFA 或 SSO；没有引入第三方云身份依赖。

## B364｜灵感演化路径检查语句拼接修复与全仓回归

- 全仓回归首次运行发现 `src/muse/inspiration_evolution.py` 的 business database 路径归一化区存在语句拼接语法错误；重新读取最新快照后，将相邻路径检查恢复为独立 `if` 语句，保留工作区目录边界、符号链接与数据库碰撞保护。
- Python 3.11 `py_compile`：本轮 6 个修改/相关 Python 文件 **6/6 passed**；`tests.test_inspiration_evolution` **15/15 passed**。
- 最终全仓 `scripts/validate_full_suite.py`：**2,415 tests / 0 failures / 0 errors / 1 skipped**（341.146 秒），WebCodex Job `wc_job_l0Na2SNhJChJX9Pl` exit 0；该最新重复回归明确记录了 1 项 skip。此前本会话全仓运行亦为 2,415 tests、exit 0。
## B365｜插件运行时安全门禁与当前平台能力边界

- 运行环境探测：Python `wasmtime`、`wasmtime` CLI、`wasmer` CLI 与 Linux `bubblewrap` 均不可用。现有 Windows Job Object 仅能限制进程数/内存/进程树生命周期，明确不提供网络或文件系统隔离。
- 因此不以普通 Python 子进程冒充沙箱，不安装/运行未验证插件，不启用自动执行；当前 `execution_enabled=false`、`executable=None`、Commander 插件请求仍经唯一 TaskQueue 并保持人工审批。
- 下一道执行门槛：由运维安装并固定可信 Wasmtime 版本，定义签名 WASM artifact 与确定性 ABI；先验证 fuel、memory、wall-time、output 上限、无 host imports/无网络/无预开放文件系统，再实现 Commander Worker adapter 与沙箱内结果丢弃/回滚契约。任一隔离能力不可证明时继续 fail closed。
- 这是运行时供应链与隔离依赖缺失的明确阻断项，不是可以通过普通 subprocess 代码补丁安全消除的问题；本轮没有虚报插件执行能力。

## B366｜商业机会到创作 Initiative 的稳定关联

- 发现商业机会晋升 Creator Initiative 后，后续视频审批与获批排程仍通过 `InitiativeStore.list(limit=200)` 搜索关联项目；当账本超过最近 200 条时，合法的旧机会可能被误报为“尚未晋升”，中断灵感到产品创作闭环。
- 新增 `_linked_initiative`：优先从商业机会 append-only `initiative_linked` 事件读取持久化 `initiative_id`，再用 `get_by_id` 精确读取，并再次验证 `business-opportunity:<id>` inspiration reference；事件缺失的旧记录才回退原有有界列表查询。无效 ID 不会被用于查找。
- TDD：新增测试先将 `InitiativeStore.list` 固定模拟为空，旧实现如预期失败（找不到已晋升 Initiative）；修复后同一测试验证审批申请与获批后排程都能通过持久化 ID 找回 Initiative。Commander 人工审批、视频 brief SHA-256 校验与“GPU execution not started”边界保持不变。
- `tests/test_business_opportunities.py`：**15/15 passed**（1.246 秒）；全仓 `scripts/validate_full_suite.py`：**2,416 tests / 0 failures / 0 errors**（341.594 秒），WebCodex Job `wc_job_6L4aoWxipyKvURT3` exit 0，内层 psmux Job `710f340dacbc48b0bbbd47accfb1f752` exit 0。
- 本修复只改善跨账本关联解析，不执行插件代码、不启动 GPU、不自动发布产品；旧事件兼容回退保留。

## B367｜全产品创作入口的精确 Initiative 定位

- 将 `LocalProductBuilder._get_initiative` 与 `StaticPrototypeBuilder.build` 从最近 200 条 `list()` 搜索改为 `InitiativeStore.get_by_id()`；保留原有 UUID 格式校验、状态门槛与构建产物完整性检查。
- 视频字幕修订不再遍历最近 200 个 Initiative 及其最近 200 条事件；现在按 `initiative_events.event_type='product_built'` 顺序扫描持久化事件，核对产品相对路径、product ID 与 version ID 三项身份，再通过 `get_by_id()` 验证 Creator Initiative 状态。已存在 Creator Initiative 的视频入账路径同样改为精确 ID 查询。
- TDD 先分别证明产品生成、静态原型生成、字幕修订、视频包恢复在 `InitiativeStore.list()` 不可用时失败；修复后四条路径通过精确 ID/事件查询完成，未放宽任何作品验证、人工字幕审批、Creator Initiative 状态或发布门槛。
- 定向回归：`tests/test_product_builder.py` **5/5 passed**；`tests/test_prototype_builder.py` **7/7 passed**；`tests/test_video_product_builder.py` **63/63 passed**。此前 B366 商业机会专项 **15/15 passed**。
- 最新全仓 `scripts/validate_full_suite.py`：**2,419 tests / 0 failures / 0 errors**（393.339 秒），WebCodex Job `wc_job_ZnudTp6_9djmFdj1` exit 0，内层 psmux Job `b3e81fc0173246b6b33e9140a424a199` exit 0。全仓回归已覆盖 B366 与 B367 所有代码变更。
- 仍保持插件 `execution_enabled=false`；本轮只修复创作入口对持久化账本的定位，不启用第三方插件、不启动真实 GPU 生成、不自动发布。

## B368｜WASM Runtime 供应链预检（保持 fail-closed）

- 当前 Windows Runner：Python 3.11.9，`wasmtime` Python 包、`rustc` 与 `cargo` 均未安装；本轮未向全局 Python 或 Muse 工作区安装未验证二进制。
- 供应链核验发现 PyPI `wasmtime 51.0.0` 页面标记为 2026-11-20 发布，但该版本文件明确没有 PyPI provenance；与此同时 Bytecode Alliance 官方 GitHub release 页面仍将 `v50.0.1` 标为已发布且 GitHub verified tag，`51.0.0` 仍显示为 unreleased。此来源/版本状态不一致不足以作为安全执行器安装依据。
- 决策：不因“最新版本”字样跳过来源验证；等待可从 Bytecode Alliance 官方签名 release/attestation 核验的 Windows x64 artifact，或建立可复现源码构建链后，再固定版本、SHA-256、ABI 与依赖清单。安装后仍须通过无 host imports、无 WASI/网络/预开放目录、fuel、memory、wall-time、输出上限和崩溃隔离测试，才可考虑注册 Worker adapter。
- 并行 Portal/Plugin 负责人修正签名测试夹具后，专项回归 **49/49 passed**（27.357 秒）；之前未签名 manifest 的 HTTP 409/202 预期不一致已解除，verified-signature 安全门槛未放宽。
- 合并最新并行改动后的最终全仓回归：**2,420 tests / 0 failures / 0 errors**（397.580 秒），WebCodex Job `wc_job_y2dedZvxfWWGUh94` exit 0，内层 psmux Job `d7f83646f9084ea7a359a9187c56fa3d` exit 0。B366/B367 创作入口修复仍包含在本轮完整回归中。
- 当前仍 `execution_enabled=false`、`executable=None`；没有把 CLI 存在或包下载成功等同于沙箱已验证。本轮没有执行第三方插件代码。

## B369｜Commander 插件审批复核修复

- 修复 `validate_approved_plugin_ticket()` 的插件任务 payload 类型/`kind` 守卫缩进损坏；复核现在先确认 payload 是对象且 `kind=plugin_capability_request`，再检查执行边界与审批记录。
- 修复审批时间戳契约不匹配：Commander `TaskQueue.approve()` 写入带时区 ISO-8601 字符串，旧复核逻辑只接受数字，导致合法审批票据无法通过 Worker 预检。现在接受带时区 ISO-8601 时间戳及有限的旧版数值时间；拒绝无时区/格式错误字符串、布尔值与非有限数字。
- 清理 `tests/test_plugin_platform.py` 中重复导入项。插件专项回归 **28/28 passed**（1.239 秒）；完整 `scripts/validate_full_suite.py` 回归 **2,421 tests / 0 failures / 0 errors**（346.510 秒），内层 psmux Job `884855b55a144a6aaa5843ee19da8037` exit 0；复核后 `muse-dev inspect` 显示 `active_job_id=null`。
- 本轮不开放第三方执行：`execution_enabled=false`、`runtime_adapter_registered=false` 仍保持；WASM 供应链来源未完成验证，沙箱与 OS 资源强制配额仍是未完成关口。

## B370｜审批时间戳极值防护与源码语句粘连清理

- 新增真实 Commander 审批票据回归，将 `approved_at` 替换为可 JSON 编码但超出 IEEE-754 有限浮点范围的整数 `10**400`；旧实现稳定复现 `OverflowError: int too large to convert to float`。修复后先安全转换再检查 `math.isfinite`，并将溢出/转换异常统一作为无效审批元数据拒绝，避免原始异常逃逸 Worker 预检边界。
- 清理 `TaskQueue._decode` 中 title/payload 同行赋值、Plugin Platform 测试中的同行断言，以及 `autonomous_research.py` 两处条件语句粘连；修复 B357 文档历史段落中被粘连的安全章节标题。上述格式整理不改变业务行为。
- 五组专项（Plugin Platform、Business Opportunities、Product Builder、Prototype Builder、Video Product Builder）**119/119 passed**（8.632 秒）；Python `py_compile` 通过，测试 AST 重复方法 **0**。最终全仓 `scripts/validate_full_suite.py` **2,422 tests / 0 failures / 0 errors**（342.666 秒），内层 psmux Job `790b3a845c2d47ff89622a868073ee46` exit 0。
- 运行时执行边界不变：`execution_enabled=false`、`runtime_adapter_registered=false`；没有执行第三方插件、没有启动真实 GPU 生成，也没有公网部署。

## B371｜Commander 人工审批时间与审计事件统一绑定

- `TaskQueue.approve()` 现在为票据 `_approval.approved_at`、任务 `updated_at` 与 `human.decision.created_at` 使用同一个 UTC 时间戳；`TaskQueue.approval_timestamp_matches_event()` 统一校验时区、格式与时间差（旧票据兼容窗口 1 秒，支持有限 Unix 数值时间，拒绝 naive/畸形/溢出时间）。
- 将统一核验接入六条审批消费路径：Plugin Platform、VideoTaskWorker、Business Opportunities 视频排队、Approved Code Change Executor、MemPalace 投递、Video Recipe Trace。只匹配审批人和 `decision=approved` 不再足够；时间戳必须与对应审计事件相符。
- 新增/调整测试覆盖 2000 年伪造时间、超大整数、naive 时间、数值时间兼容，以及视频/代码/MemPalace 执行路径拒绝伪造审批。七组专项合计 **190/190 passed**（25.381 秒），14 个实现/测试文件 `py_compile` **14/14 passed**。
- 最终全仓 `scripts/validate_full_suite.py` **2,427 tests / 0 failures / 0 errors**（342.297 秒），内层 psmux Job `3c0f97debb4d4828babac6303f417c27` exit 0。
- 此项只强化人工审批审计完整性；`execution_enabled=false` 与 `runtime_adapter_registered=false` 继续保持。WASM 供应链验证、沙箱隔离和真实资源配额尚未完成。

## B372｜Wasmtime 51.0.0 官方运行时预检

- 核对 Bytecode Alliance 官方 GitHub Release：Wasmtime **51.0.0**（2026-11-20），Release immutable，tag commit 显示 GitHub Verified 签名；Windows x86-64 资产 `wasmtime-v51.0.0-x86_64-windows.zip` 官方 SHA-256 为 `d2f5853a1cded9b1256b45cd57686b6855e69049ef07fd4ad78feb78dfa3aada`。官方发布页还显示 50.0.3 修复了 Windows WASI drive-prefixed path 文件系统沙箱逃逸，因此不选 50.0.0。
- 已通过 GitHub CLI 下载官方 zip 到 `D:\AI AGENT\Muse\.tools\wasmtime\staging\`；本地 `Get-FileHash SHA256` 与官方 digest 完全一致（12,560,257 bytes）。同步取回 GitHub Release attestation bundle；其 `https://in-toto.io/attestation/release/v0.2` subject 含同一 Windows zip SHA-256。
- 完整签名验证尚未完成：本机 `gh 2.105.0` 未登录。`gh attestation verify` 默认 SLSA provenance predicate，不适用于本 Release 的 `release/v0.2` predicate；正确验收命令是 `gh release verify-asset v51.0.0 <zip> --repo bytecodealliance/wasmtime`，但当前因 GitHub CLI 未认证而要求 `gh auth login`。未读取/导出任何 token。
- **未解压、未安装、未执行**暂存二进制；不将“哈希匹配 + bundle 内容读取”冒充签名验证。等待用户在本机完成 GitHub CLI 交互式登录后，再执行 `gh release verify-asset`；通过后才可解压至版本固定目录并开展 Wasmtime host-import/fuel/memory/WASI capability tests。
- 目前 `execution_enabled=false`、`runtime_adapter_registered=false` 保持；尚无 WASM Worker adapter、无网络/文件系统 host imports、资源配额强制与异常回滚验收。

## B373｜Portal 首次管理员高熵凭证生成器

- `muse.web_portal_admin` 新增 `--generate-initial-password`，通过 `secrets.token_urlsafe(48)` 生成 48-byte CSPRNG（384-bit）URL-safe 首次管理员口令；与密码轮换 CLI 互斥，不需要 Session DB，不触碰现有凭证。
- 保持既有单管理员模型和 scrypt 持久化摘要；不把口令写入数据库、源代码或文档。部署指南明确生成命令必须在受信任的本机交互终端执行，不可经 psmux/CI/日志/重定向捕获，生成后存入密码管理器并通过受保护服务环境注入。
- TDD：新增生成器测试先 RED（缺少 `generate_initial_password` 导入失败），实现后 GREEN；Web Portal + Deployment **22/22 passed**（73.647s），Python 全仓 `scripts/validate_full_suite.py` **2,434 tests / 0 failures / 0 errors**（343.187s），`py_compile` **2/2 passed**。
- 多用户/RBAC、实际公网域名/证书/代理配置仍未配置或验收；此改动不把单管理员 Portal 宣称为多用户 SaaS。

## B374｜灵感演化改为 Commander 异步任务闭环

- `POST /api/v1/inspirations/evolve` 不再在 HTTP 请求线程直接运行 `evolve_inspiration_closed_loop()`；现在验证 32 位请求 ID、灵感 ID、视频开关和画幅，再写入 Commander `TaskQueue`（research workload），返回 **202 Accepted + task_id**。
- `AutonomousSupervisor` 增加严格 allowlist handler，并且仅通过 `_next_local_workflow_id()` 选择 `inspiration_evolution` 任务；继续复用现有 Worker lease、heartbeat、failure classification 与 TaskQueue lifecycle。没有引入第二个线程调度器，也没有让 Web Portal 直接运行模型/研究流程。
- 请求 ID 绑定幂等键；重复提交在排队、执行或完成后均返回同一 Ticket。完成态 `_result` 不参与请求内容比较；同一 ID 携带不同参数会被拒绝。非法画幅类型/重复画幅会在入队前拒绝。
- Web Portal 使用 `crypto.randomUUID()` 生成 request ID，失败重试复用同一按钮上的 ID；提交后显示 Commander Ticket ID，任务列表显示 queued/running/succeeded/failed。网页不会自动执行代码或启动视频生成，相关执行仍走独立审批。
- 验证：Status API → TaskQueue → AutonomousSupervisor 的集成测试验证“提交后仍 queued、重复请求不重复入队、不同 payload 冲突拒绝、后台 tick 完成后回链 blueprint_ready”；Web Portal TLS/CSRF/upstream proxy 集成验证 202 透传。四个模块组 **66/66 passed**（85.935s），`py_compile` **6/6 passed**，`node --check web-portal/app.js` passed。最终全仓 `scripts/validate_full_suite.py` **2,437 tests / 0 failures / 0 errors**（344.600s），内层 psmux Job `5f568173dffe48cba9c714790fa24804` exit 0。
- 运行条件：必须有 `AutonomousSupervisor` 常驻进程处理队列；若 runtime 停止，Ticket 会安全地留在 `queued`，Portal 不会伪称已完成。本轮只在隔离测试目录启动 Supervisor，没有启动用户真实运行时或真实 GPU 视频任务。额外检查目标 Windows 主机进程表，未发现 `muse.autonomous_runtime` / `autonomous_runtime.py` 常驻进程；所以真实 Portal 提交会先排队，直至用户启动 runtime。启动入口为项目根目录 `py -3.11 -m muse.autonomous_runtime run`（前台常驻，暂停/恢复/停止可用对应 CLI 子命令）。

## B375｜Commander Runtime 健康状态进入 Portal

- `/api/v1/overview` 现在附带 `autonomous_runtime` 安全投影：仅包含 `state`、`running`、`heartbeat_age_seconds`、`stale`；不暴露 owner ID、PID、控制日志路径或内部错误文本。
- Portal 仪表板增加 runtime 状态 pill，区分 ONLINE、HEARTBEAT STALE、STOPPED；runtime 停止时明确提示 Commander tickets 会留在队列，避免用户误以为后台正在执行。
- TDD：overview API 断言先 RED（缺少 `autonomous_runtime`），实现后 GREEN。Status API + Portal + Deployment **71/71 passed**（100.794s），`py_compile` **2/2**，`node --check web-portal/app.js` passed；最终全仓 **2,437 tests / 0 failures / 0 errors**（342.365s），psmux Job `ac48c2bc01444898a0627f138afccff3` exit 0。
- 当前主机仍未发现 AutonomousSupervisor 常驻进程；Portal 会显示 STOPPED。未擅自启动常驻后台研究或视频生成任务。

## B376｜Portal 常驻运行时生命周期控制

- 将 Status API 已有的 daemon `start/pause/resume/stop` 四个操作以严格 allowlist 代理到 Portal；不开放 `once`、任意路径或任意方法。控制请求必须经过登录 Session + CSRF 校验，body 只接受一个 action 字段。
- Portal 新增启动/暂停/恢复/停止按钮，依据 runtime `state/running/stale` 自动启停按钮；停止操作额外要求浏览器确认。启动说明明确会运行已启用的维护/研究例程，代码修改、插件执行和视频生成仍保留独立审批门槛。
- TLS 集成测试验证无 CSRF 的 start 返回 403、非法 restart 返回 400 且不触达上游，start/pause/resume/stop 四个合法动作正确转发；按钮静态资源可见。Status API + Portal + Deployment **71/71 passed**（113.405s），`py_compile` **3/3**，`node --check web-portal/app.js` passed。
- 最终全仓 `scripts/validate_full_suite.py` **2,437 tests / 0 failures / 0 errors**（355.795s），psmux Job `634be5100b0c41dba34fbdd6a4bc67d6` exit 0。该轮只测试控制代理，不点击启动按钮；目标主机 runtime 仍为 STOPPED，未启动真实后台例程。

## B377｜Daemon 启动失败状态回滚

- 修复 `MuseDaemonController.start()` 的状态假在线边界：从 paused/stopping 预置 `running` 后，如果日志目录/文件无法创建或子进程 `Popen` 失败，不再把 runtime 留在 `running`。
- `RuntimeStateStore.reset_unowned()` 使用单条条件 UPDATE，只在 `owner_id IS NULL` 时将状态复位为 stopped；若另一 Supervisor 已抢先取得 lease，回滚不会误停正在运行的实例。
- 日志打开失败时关闭已成功打开的句柄，避免 stdout 成功而 stderr 失败时泄漏文件描述符。
- RED/GREEN 故障注入：Popen spawn denied、log disk full 两种启动失败均回滚为 stopped；并发 owner 获得 lease 后，rollback 不会覆盖 owner。DaemonControl **10/10 passed**（0.230s），相关 Python `py_compile` **3/3**。
- 交叉回归：Daemon + Autonomous Runtime + Status API + Portal + Deployment **108/108 passed**（116.285s），Node syntax check passed；最终全仓 `scripts/validate_full_suite.py` **2,440 tests / 0 failures / 0 errors**（357.205s），psmux Job `f2277ee341454159879cc70b185bbcb6` exit 0。
- 未启动真实常驻 Runtime；该修复只保证启动失败时状态正确，不改变产品的后台运行授权与审批门槛。

## 主进度文档备注

本轮尝试追加 `docs/PROGRESS.md` 时，文件编辑器两次检测到该文件混用 LF/CRLF 并拒绝事务，确认未对该文件作任何修改。为避免整份 3,700 行进度文件换行符归一化造成无关变更，本记录单独保存于本文件；后续可在统一换行符的专门维护步骤中合并索引。

## B378｜插件票据禁止进入通用 LLM 调度器

- 沿着 Commander TaskQueue → `dispatch_once` / `dispatch_commander_once` → Worker 边界审计时，发现插件能力请求虽然被标记 `execution_enabled=false` 与 `execution_adapter_required=true`，但两个通用调度入口此前没有识别该票据类型；在 provider 已配置时，存在被误当作普通任务交给 Ruflo/Commander DAG 的路径。
- TDD：新增回归测试先 RED（`RuntimeBlocked` 未抛出）；随后在两个通用入口的 claim 前增加同一 fail-closed 检查，凡 `kind=plugin_capability_request` 或 `execution_adapter_required=true` 均拒绝通用调度，且不 claim、不改变 queued 状态、不调用 Ruflo。未来只能由独立注册并通过沙箱验证的专用 Worker 处理。
- Python 3.11 经 `scripts/muse_dev.py run` → psmux：新回归 + `tests.test_plugin_platform` **31/31 passed**（1.472s，WebCodex Job `ec918bb2735f4a8fa1012ae775bf0b98` exit 0）。
- 最终全仓 `scripts/validate_full_suite.py`：**2,441 tests / 0 failures / 0 errors**（357.463s），WebCodex Job `wc_job_hVC9i-pCsUcS66K7` exit 0；此前定向 `tests.test_unified_runtime` + `tests.test_plugin_platform` **85/85 passed**（9.448s），相关 Python `py_compile` passed。
- 本修复没有启用插件执行、没有注册假 Worker、没有解压或执行 Wasmtime artifact；未验证的沙箱能力仍 fail closed。

## B379｜插件运行中票据绑定真实 Worker 租约

- `validate_approved_plugin_ticket()` 对 `running` 状态不再只看任务状态：必须提供当前 `worker_id`，与 TaskQueue `lease_owner` 精确匹配；租约到期时间必须是带时区的有效未来时间；attempt 必须是正整数；并且必须存在 actor、attempt 与当前票据相符的 `assignment.claimed` 审计事件。
- 缺失 Worker 身份、其他 Worker、过期/无效租约、缺失或不匹配的 claim 审计均 fail closed。`queued` 仍可作审批前置复核，但所有返回仍保持 `execution_enabled=false`、`runtime_adapter_registered=false`，不构成执行许可。
- 新增回归覆盖当前/错误 Worker、过期租约、删除 claim audit；定向联合回归（Plugin Platform + Autonomous Research + Feedback Experiment）**109/109 passed**（44.555s），Feedback Experiment 的隔离嵌套套件 **69/69 passed**（19.360s），相关 `py_compile` 通过。

## B380｜低证据研究分支的视频任务默认状态

- 最新合并全仓测试先发现 3 个 `AutonomousResearchEngine` 分支错误：低证据、象征性候选及不完整反馈研究在 `high_value=False` 时不会创建视频任务，但报告仍读取未初始化的 `video_task`，触发 `UnboundLocalError`；反馈研究实验随后因嵌套研究套件失败而返回 `failed`。
- 在高价值视频审批分支之前初始化 `video_task={"status":"not_queued_low_evidence"}`；高价值路径仍由原有人工审批流程覆盖该值，不改变视频审批与 GPU 启动边界。
- 修复后针对 7 个关键回归 **7/7 passed**（24.233s），其中反馈实验隔离嵌套套件 **69/69 passed**。最终 `scripts/validate_full_suite.py` **2,456 tests / 0 failures / 0 errors**（363.170s），WebCodex Job `wc_job_McrSiNWEwsH_35lf` exit 0。
- 本轮仍未启用插件执行、未解压/安装/执行 Wasmtime artifact，未启动真实 AutonomousSupervisor；Wasmtime 签名验证与专用沙箱 Worker 仍是外部安全门槛。

## B381｜TaskQueue Worker 租约审计与续租一致性

- TaskQueue.claim_next() 现在在同一领取事务内记录 attempt/lease_epoch、稳定 lease_id（task_id + epoch）、heartbeat_at 与 lease_expires_at；heartbeat() 在同一 SQLite savepoint 内更新租约并写入 assignment.lease_heartbeat，两者原子提交。
- 心跳续租要求当前状态仍为 running、Worker 身份匹配且原租约尚未过期；过期 Worker 不能复活旧租约。使用 SAVEPOINT 支持调用方已有事务，失败时只回滚本次心跳子事务，不擅自提交或回滚调用方事务。
- validate_approved_plugin_ticket() 对运行中票据核对最新 claim/heartbeat audit 的 actor、attempt/epoch、lease_id、heartbeat_at 与 lease_expires_at；数据库租约被无审计地延长或审计不匹配时拒绝。Watchdog 将 lease heartbeat 排除在“有意义进展”基线之外，避免心跳掩盖任务停滞。
- TDD 回归覆盖无审计延长租约拒绝、claim/heartbeat 字段绑定、过期 Worker 不可续租、嵌套事务 savepoint 保留调用方回滚语义、审计写入失败时租约更新完整回滚，以及 Watchdog 心跳不冒充业务进展。
- 专项联合回归：TaskQueue + Plugin Platform + Watchdog 101/101 passed（6.632s）；新增审计写入失败原子回滚测试后，TaskQueue 51/51 passed（1.614s）。最终 scripts/validate_full_suite.py：2,475 tests / 0 failures / 0 errors（371.611s），WebCodex Job wc_job_SzagDSSdA1x0k6N_ exit 0，内层 psmux Job 939f050b14454c248dd7c6bd40347899 exit 0。
- TOCTOU 边界说明：当前插件票据复核仍是 fail-closed 预检，不是执行 Grant；execution_enabled=false、runtime_adapter_registered=false 继续保持。未来专用 Worker 必须在真正执行前重新取得/验证当前租约，并实现可原子 fencing 的执行入口；不能把本次只读复核当作执行授权。
- 未启动真实 AutonomousSupervisor，未执行第三方插件代码，未解压/安装/执行 Wasmtime。

## B384｜封堵 Status API 的 prompt-to-video 直通执行

- 端到端路由审计发现旧 `POST /api/v1/video-products` 在 `mode=prompt_to_video` 时会由 Status API 自行启动 `MuseVideoProduct` 线程，并直接调用 `VideoProductBuilder.build_from_prompt()`；该入口绕过 Creator Initiative 对应的 Commander 审批票据、VideoAdmissionRuntime、GPU lease 与成本门禁。
- Status API 现在对合法的 `prompt_to_video` 请求返回 HTTP 409、错误码 `video.commander_admission_required` 和 `approval_required=true`，不创建视频 Attempt、不启动线程、不调用 Wan；旧线程执行器内部也拒绝 prompt-to-video，避免未来内部调用误走直通路径。
- `local_assets` 图片/音频素材 FFmpeg 路径保持可用。AI 视频正式执行仍走 Creator Initiative → Commander 持久审批 → VideoTaskWorker → Video Admission；不把 HTTP 409 误报成任务已排队。
- 新增/更新 API 回归，验证直接生成请求被拒绝、Wan/Builder 未调用且没有产品副作用。Python 3.11 经 `scripts/muse_dev.py run` → psmux：Video Product API **5/5 passed**、Status API **49/49 passed**、Video Admission Runtime **13/13 passed**、VideoTaskWorker **38/38 passed**，合计 **105/105 passed**。
- 最终 `scripts/validate_full_suite.py`：**2,475 tests / 0 failures / 0 errors**（415.165s），WebCodex Job `wc_job_es_BRLuwZT4pr0ft` exit 0；`muse-dev inspect --session muse-dev` 确认 `active_job_id=null`。
- 本轮没有启动真实 AutonomousSupervisor、没有触发 Wan/ComfyUI/GPU 渲染、没有发布产品。Video Admission 仍需真实设备/服务验收；插件执行仍保持 disabled，TOCTOU execution fencing 仍是独立安全门槛。

## B385｜全仓测试 workspace 隔离事故与修复

- 复盘发现 `tests/test_autonomous_research.py` 有 10 个调用 `AutonomousResearchEngine.run_once()` 的测试构造器没有传 `workspace`；`AutonomousResearchEngine` 默认 `workspace=Path.cwd()`，研究周期会写入网站目录、哲学记忆库，并在高价值分支更新 InitiativeStore。现已为这 10 个测试全部显式传入 `workspace=root`。
- B384 首次全仓 Job `wc_job_es_BRLuwZT4pr0ft` 在项目根目录 `.` 运行。只读 SQLite 核对确认测试时间窗内正式工作区数据库被测试副作用写入：`data/initiatives/muse.sqlite3` 的 `initiative_knowledge_links` 新增 22 条（涉及 2 个既有 initiative ID；没有新增视频 Attempt）；`data/philosophy/muse.sqlite3` 新增 66 experiences、204 links、2 个 pending principle proposals；`data/research/websites.sqlite3` 新增 42 个 site events。相关哲学记录的 source_ref 指向临时测试报告目录，确认这些是测试产物而非新研究结论。
- 修复后，第二次全仓从独立 cwd `.muse-test-sandbox` 运行：Job `wc_job_MfAQXzRsNB42SKKu` **2,475 tests / 0 failures / 0 errors**（373.965s，exit 0）；同一隔离快照的复核 Job `wc_job_bJ1AsZmhjdXRAYIj` **2,475 tests / 0 failures / 0 errors**（372.839s，exit 0）。两次测试结束后只读核对项目根目录三份 DB 的 mtime 均保持首次事故时间戳（17:27:41 UTC），没有被隔离回归再次写入；新增测试数据只留在 sandbox 下。- 之后所有研究测试必须显式使用临时 workspace；全仓执行 cwd 必须固定到独立测试 sandbox。禁止在项目根目录再次运行全仓或 AutonomousResearch 测试。