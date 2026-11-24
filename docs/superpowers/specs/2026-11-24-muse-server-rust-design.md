# Muse Server（Rust）设计文档

> 日期：2026-11-24
> 类型：新建项目（在 Muse 仓库内增量实现）
> 状态：已确认（创造者 2026-11-24 决定：按设计默认实现；发布矩阵另行调整；新增方向：全平台生态统一用 Rust）
> 适用范围：远程服务器版 Muse，不替换现有桌面 Tauri 壳与本地 Python 运行时

## 1. 意图与为什么

创造者希望 Muse 拥有一台 24 小时运行的服务器版本：人可以在任意浏览器里查看状态、派发任务、查看成果与进度、进行干预；服务器本身能自主运行、自主开发、自主进化。

选择 Rust 做服务器外壳的理由：单二进制部署、内存占用低、并发与网络生态成熟、适合长驻服务与反向代理；同时与现有 `desktop/src-tauri` 的 Rust/Tauri 技术栈保持一致，避免再引入一套异构控制平面。

这是**新增服务器入口**，不是把现有 Commander、TaskQueue 或 SQLite 账本搬走重写。Muse Commander 仍是唯一调度权威。

## 2. 目标

- 远程可访问：浏览器打开一个地址即可看到真实状态、任务、成果、进度。
- 可干预：暂停/恢复/停止、审批任务、取消任务、设置预算与并发限制。
- 可派任务：通过界面或 API 提交目标/提示词，进入既有 TaskQueue / Commander 流程，等待审批后执行。
- 可自主运行：服务器进程内常驻调度循环，按周期触发既有 routines/autonomy/research 能力，不依赖某个人一直开着对话框。
- 可自主开发与进化：只通过既有「研究提案 → 来源/授权审查 → Ticket → 专家执行 → 测试 → 独立 Reviewer → 人工批准 → 整合 → 经验回写」闭环；服务器负责承载与展示，不发明新的自我授权路径。
- 可视成果：展示本地产物（研究报告、原型、视频 QA 报告等）的元数据与受控下载，不暴露任意主机路径。

## 3. 非目标

- 不重写 Python 核心运行时（`src/muse/`）、Commander、TaskQueue、Research Wiki、视频工厂。
- 不创建第二套任务队列、调度器或权限中心。
- 不在首版启用动态插件执行、任意模型生成命令自动执行、自动对外发布、自动付款或收益闭环。
- 不把玄学推演包装成事实证据。
- 不在首版实现多租户/公网开放注册；服务器先按单owner/可信网络设计。

## 4. 反目标（即使技术上做出来也算失败）

- 服务器 UI 显示伪造、缓存过期的状态，或把「已排队」说成「已完成」。
- 用户点「批准」后绕过既有人工审批审计与独立审查门禁。
- 浏览器能读取任意本地文件、任意数据库、任意 shell 命令，或越权访问 `data/`、`artifacts/` 原始路径。
- 自主进化变成模型直接改生产代码、自我批准、无测试合并。
- 多个用户互相看到私人记忆或私人产物。

## 5. 约束与依赖

- 现有权威源：`data/tasks/muse.sqlite3`（任务）、`data/progress/muse.sqlite3`（进度事件）、`data/capabilities/muse.sqlite3`、`data/agents/muse.sqlite3`、`data/improvements/muse.sqlite3`、`data/research/knowledge.sqlite3`、`data/initiatives/`（若适用）。
- 现有 HTTP 契约：`src/muse/status_api.py`（Status API v1）、`src/muse/web_portal.py`（Web Portal，含登录、灵感、产品库、运行控制）、`docs/API_CONTRACT.md`。
- 现有 Web 前端：`web-portal/`（原生 JS + SW PWA）、`ui-prototype/`（Svelte 桌面 UI）。优先复用，不重写一套 React 管理后台。
- 运行时：Python 3.11+ 承担实际 Agent/研究/视频/工作流执行；Rust 承担 HTTP 服务、静态资源、认证、代理与生命周期管理。
- 部署：Linux x86_64 是首要目标；Windows/macOS 作为可运行平台尽力支持。
- 安全：loopback-only 默认；公网必须显式配置域名、TLS、强密码与可信代理；不使用明文 token 落盘；凭据从环境变量/secret 读取。

## 6. 方案选择（我的决定，非创造者已确认）

### 6.1 Rust crate 与职责

新增 `server/rust/`，一个独立 Cargo workspace member（或独立 crate）：

- `muse-server` 二进制：启动 HTTP 服务、静态托管前端、反向代理到现有 Python Status API/Web Portal、管理 Python 子进程生命周期、提供少量 Rust 原生控制端点。
- 依赖候选：`axum` + `tokio`（HTTP/async）、`tower-http`（CORS/静态文件/压缩）、`serde`/`serde_json`、`tracing`、`clap`、`reqwest`（可选，代理）、`jsonwebtoken` 或自研 HMAC session（登录态）。具体 crate 版本在实现计划中定。
- Rust 不直接写业务 SQLite；所有业务读写仍经 Python API。Rust 只读少量运行元数据用于 health 显示，或通过子进程调用 Python CLI 的结构化输出。

理由：现有 Python API 已经有业务语义、审计和权限；Rust 直接写库会复制权限逻辑，形成第二控制平面。

### 6.2 前端

首版直接服务 `web-portal/` 的静态文件，通过 Rust 反向代理 `/api/v1/*` 到 Python Status API/Web Portal。必要时在 `web-portal/` 增加「服务器模式」页面段落：runtime control、任务派发表单、成果列表、进度时间线、审批/取消按钮。保留 PWA service worker，但缓存策略需避免显示过期状态。

`ui-prototype/` 桌面 UI 暂不并入服务器首版，除非创造者要求统一视觉。

### 6.3 自主运行

使用现有 `muse.autonomous_runtime` / `muse.routines` / `muse.autonomy` 作为后台执行体。Rust 服务启动时可根据配置拉起一个受管的 Python 自主运行子进程：

- 进程由 Rust 持有 PID 与句柄，只管理自己启动的子进程。
- 停止服务时先发 stop 信号，再等待/超时 kill；不杀外部 LM Studio/ComfyUI。
- 运行状态读取现有 `RuntimeStateStore.status()` 语义，避免把正常 stop 误报为 stale。
- 自主开发的实际执行只能在获批时进入既有 Worker/Commander 流程；Rust 不直接执行模型生成命令。

### 6.4 安全与暴露

- 默认绑定 `127.0.0.1:8766`（与现有本地控制台一致），配置 `MUSE_SERVER_BIND` 可覆盖。
- 公网部署要求显式 `MUSE_SERVER_PUBLIC_URL`、`MUSE_SERVER_PASSWORD_HASH` 或等效强凭据、`MUSE_SERVER_TRUSTED_PROXY` 配置；缺失任一则拒绝以 public 模式启动（fail-closed）。
- 所有 `/api/v1/portal/*` 控制端点需要登录态与 CSRF 防护；Rust 代理不绕过 Python 端鉴权。
- 成果下载只走现有产品库白名单代理；不新增任意路径读取端点。
- 首版单用户；多用户/管理员角色列为未来项。

### 6.5 与自动发布的关系

更新 `.github/workflows/scheduled-release.yml` 和 `docs/GITHUB_RELEASES.md`：构建矩阵增加 `muse-server` 原生二进制产物（Linux musl/glibc、Windows、macOS），打包为 tar.gz/zip 并加入 Release。服务器二进制内置静态前端与配置模板；Python 运行时由用户环境或安装包提供，首版不打包整个 Python 解释器。

## 7. 已确认决策（创造者 2026-11-24）

1. 安全模型：单用户 + 可信网络/本机优先；公网开放与多租户留到后续。
2. 执行边界：实际 Agent 执行继续由本机 Python 运行时承担，Rust 只做服务外壳与生命周期管理。
3. 前端：浏览器首版复用现有 `web-portal/`，不重写 `ui-prototype/` 的 Svelte 桌面视觉。
4. 端口：Muse Server 作为独立服务并存，默认使用不同端口（见 §6.4），不强制替换现有 8766 本地控制台。
5. TLS：首版由前置可信反向代理终止，Rust 只绑 loopback；Rust 内终止 TLS 列为未来项。
6. 自主运行依赖：本机模型/凭据若不可用则 fail-closed，不擅自付费或伪造派发；具体端点由配置提供。
7. 发布矩阵（创造者新增要求）：
   - Linux：在 glibc 之外增加 `aarch64-unknown-linux-gnu`（Arch Linux ARM）目标；保留 x86_64。
   - Windows：去掉 `.msi`，改为 `.zip` 便携版；保留 `.exe` 安装包。
   - macOS：只生成 `.dmg`，去掉 `.app.tar.gz`。
   - 计划增加 Android 手机版与 Android TV 版：当前先记录为规划，待 Android SDK/NDK 与 Tauri Android 条件就绪后启用。

### 新增方向：全平台生态统一用 Rust

创造者提出“全平台生态统一用 Rust 语言”。本设计把它记为**长期技术方向**，不是本轮立即执行的重写指令：

- 现状约束：现有 Python 运行时承载 Commander、TaskQueue、记忆、研究、视频、审查等完整业务语义与审计；桌面壳已经是 Rust/Tauri；Web 前端是原生 JS。
- 本轮范围：只新增 Rust 服务器外壳；不因为“统一 Rust”而中断或并行重写仍在演进的 Python 核心，避免同时维护两套业务实现、形成第二控制平面。
- 统一路径（规划）：先把所有平台的**外壳、服务、客户端、发布物**统一为 Rust（桌面 Tauri、服务器 Rust、未来的 Android/TV 客户端 Rust/Tauri），通过稳定的本地 HTTP/IPC 契约调用核心能力；核心能力按模块、有测试与等价验收后再逐步迁移到 Rust 库，迁移一块、退役一块 Python 对应模块，不搞大爆炸重写。
- Android 手机版与 Android TV 版按此方向采用 Rust/Tauri 客户端路线；当前仍为规划，待 SDK/NDK 与构建条件就绪后启用。

### 实现细节默认决定（审查后收敛）

- Rust 采用独立 crate `server/rust/`，不建根 workspace；首版不新增 Rust 原生业务端点，仅 health/ready/liveness、静态资源与反向代理。
- 鉴权完全交给现有 `PortalSessionStore`（scrypt + token + CSRF），Rust 不自引 JWT、不复制凭据体系；所有 `/api/v1/*` 在服务端 `Cache-Control: no-store`，SW 对 API 走 network-only。
- 凭据形态收敛为单一 `MUSE_SERVER_PASSWORD_HASH`（scrypt，与 `web_portal.py` 一致）；公网模式强制前置 TLS 反向代理，缺失配置 fail-closed。
- server 直接打包 `web-portal/`（原生 JS，无需构建），CI 中 server 不依赖 `ui-prototype` 的 npm 构建产物。
- 启动时校验 `python3.11+` 可执行，`MUSE_PYTHON` 可覆盖；不管理外部 LM Studio/ComfyUI 进程。

## 8. 验收草案（进入实现计划时细化）

- 从远程浏览器登录，能看到真实 Status API 的 health、任务队列统计、进度事件、Agent 状态、成果列表。
- 派发一个 echo/安全示例任务，进入 waiting_approval，审批后由 Worker 执行，UI 显示状态变化与时间线。
- 暂停/恢复/停止自主运行，状态与现有 RuntimeStateStore 一致，无 stale 误报。
- 触发一次安全的研究/灵感流程，产出可在成果列表查看，反馈可导入 Research Wiki 并回链源 claim。
- 任意路径读取、越权控制、未登录访问、伪造 CSP 产品预览均被拒绝。
- 服务器二进制构建产物在 CI 中生成并出现在每 3 天的 Release 中。
- CI 产物符合调整后的矩阵：Linux x86_64 与 aarch64、Windows zip 便携版（无 msi）、macOS 仅 dmg（无 app.tar.gz）；Android/TV 仅作为规划条目，不生成产物。
