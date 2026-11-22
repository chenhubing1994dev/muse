# B350 — Muse Companion 独立 TLS 传输层

日期：2026-11-22

## 本轮目标

在不修改桌面 Status API 的 loopback + Bearer 边界、不将桌面令牌交给手机的前提下，落地独立 Companion TLS listener，把已有配对安全核心接入真实 HTTP/TLS 请求路径。

## 已落地

- 新增 src/muse/companion_transport.py，与桌面 status_api.py 完全分离；不会自动启动，也不会改变现有桌面 API 监听地址。
- 新增 muse-companion-host CLI entry。必须显式提供 Companion 数据库、Commander task/progress 数据库、TLS 证书和私钥；默认只绑定 127.0.0.1，端口默认 8766。
- TLS server context：
  - 必须加载独立证书与私钥；缺失、路径不是普通文件、证书无效或密钥不匹配时 fail-closed。启动前还要求 Companion/Commander task/Commander progress 三个数据库都已存在且为普通文件，路径错误时不自动创建空数据库。
  - 最低 TLS 1.2，最高 TLS 1.3；关闭 TLS 压缩，只协商 HTTP/1.1。
  - 不读取 X-Forwarded-* 或其他代理头作为 TLS 证明。
  - TLS 握手在有界 worker 中执行，最多 8 个并发连接/握手；握手超时 5 秒，请求 socket 超时 10 秒。
- 配对 API（仅 TLS）：
  - POST /api/v1/companion/pairing/request：挑战码、设备展示名、fingerprint 与客户端 claim secret 进入现有 CompanionDeviceStore；Host 仍须通过本机设备安全面板批准。
  - POST /api/v1/companion/pairing/claim：批准后一次性领取设备 ID 与随机设备 token；响应设置 Cache-Control: no-store / Pragma: no-cache。
- 设备只读 API（TLS + HMAC）：
  - GET /api/v1/companion/status：复用 Commander TaskQueue 和 ProgressEventStore，只返回队列状态计数、Worker 聚合计数及设备权限，不返回任务标题、任务 payload、聊天内容或私有标识。
  - GET /api/v1/companion/events：有限批次 JSON 事件，返回持久事件 ID 作为游标；只暴露 category/event/created_at/error_code，不暴露 subject_id 或自由文本 summary；旧游标返回 reset=true。
  - HMAC 覆盖 path、method、device/session ID、nonce、时间戳与 Last-Event-ID；nonce 仍由 SQLite 原子消费。撤销设备立即失效。
- 仅允许配对 POST 与签名 GET；PUT/PATCH/DELETE/OPTIONS 明确拒绝。拒绝 query string、Transfer-Encoding、超限 body 和非 JSON body。
- 请求日志不记录 URL、query、配对码、claim secret 或设备 token。
- CORS 只允许 Tauri WebView 的精确 Origin allowlist；OPTIONS 预检按路由、GET/POST 方法和请求头逐项校验，不使用通配符、不允许 credentials；其他 Origin 在请求入口被拒绝。
- 新增 ui-prototype/api.js 的 createCompanionRemoteClient：HTTPS only、系统 CA/主机名校验交给 WebView TLS 栈、禁止重定向、omit cookies、no-store、no-referrer；WebCrypto 生成 claim secret/fingerprint/session/nonce，并按服务端 canonical 格式生成 HMAC-SHA256。绝不使用桌面 Bearer。拒绝 localhost、loopback、RFC1918/LAN 与 link-local 地址，跨网必须使用公网可达 HTTPS 域名或可信隧道域名。
- 新增 MobileCompanionPanel.svelte：手机端录入 Host HTTPS 地址与 Host challenge ID/code，提交配对、等待 Host 人工批准、领取只读凭据、刷新状态/事件；移动端导航只显示 Companion 页。Host 面板现在展示 challenge ID + code 并可复制配对 JSON。
- 设备 token 与 claim secret 当前仅驻留 App 进程内存，不写 localStorage/IndexedDB/普通配置文件；退出/重载需重新配对。Android Keystore / Apple Keychain 尚未接入。
- Tauri CSP connect-src 仅额外开放 https:，未开放任意 http: 远程连接。
- 新增 tests/test_companion_transport.py，使用临时自签名证书验证真实 TLS 握手、服务端证书/主机名验证、配对/批准/领取、状态读取、事件脱敏/游标签名/重放拒绝、Tauri CORS、明文 HTTP 拒绝、无效证书及错误数据库路径 fail-closed。
- pyproject.toml 注册 muse-companion-host 命令；没有增加运行时第三方依赖（测试用 cryptography 仅用于临时生成证书，缺失时该测试类会跳过）。

## 验证

- Companion security + TLS transport 专项：**19 tests / 0 failures / 0 errors**（Python 3.11）。
- UI TAP/API 测试：**60/60 passed**；Svelte check **0 errors / 0 warnings**；Vite production build **149 modules，成功**。
- Python 3.11 全仓回归：**2,356 tests / 0 failures / 0 errors / 1 skipped**；compileall 通过。包含本轮新增 Companion transport、CORS、数据库路径检查以及最新 video admission 修复。
- SkillFusion 专项 **9/9 passed**；Video Commander Admission 专项 **9/9 passed**。
- 新增 transport 模块、测试文件 py_compile 通过；pyproject TOML 解析及 muse-companion-host CLI --help 通过。
- Windows Rust/Tauri：`cargo fmt -- --check` 通过；`cargo test --offline` **9/9 passed**；`cargo clippy --offline --all-targets -- -D warnings` 通过、0 warnings；`cargo build --release --offline` 成功。
- 新 Release EXE：`desktop/src-tauri/target/release/muse-desktop.exe`，9,024,000 bytes，SHA256 `ab884fabcdfab5cfff9d861928421bdf85dc8b880df560d542f9a6a336018429`。未生成 MSI/NSIS 安装包。
- TLS 测试客户端使用 CA 验证与 localhost SAN 校验；不是 CERT_NONE；额外测试确认未知 CA 与主机名不匹配均失败。
- 专项验证覆盖设备 token 一次性返回、no-store、HMAC nonce 重放拒绝、事件游标绑定、事件隐私脱敏、Tauri WebView CORS 预检/非允许 Origin 拒绝、未知 CA/错误主机名拒绝、明文 HTTP 无法访问 TLS listener，以及数据库路径不存在时 fail-closed 且不创建空文件。

## 明确未完成

1. TLS listener 仍是独立 CLI 服务，不由 Tauri Supervisor 自动启动；桌面 UI 尚无监听状态、证书配置与启停控制。
2. 默认只绑定 loopback。尚未配置 DNS、公网防火墙、反向隧道或用户实际可达的跨网络入口；本轮没有对公网开放端口。移动端 UI/HTTPS 客户端已接通协议，但没有实际外网 Host 可供端到端联调。
3. 移动端 WebView 使用系统 CA 与主机名验证；证书 pinning 尚未实现。当前不允许自签名且不受系统信任的证书。
4. 移动端 Host 地址录入、challenge ID/code、申请/领取流程已实现；暂不支持二维码扫描。claim secret 与 device token 仅内存保存，Android Keystore / Apple Keychain 尚未接入，退出/重载需重新配对。
5. Companion 状态采用有限 JSON 轮询批次，不是 SSE 长连接；当前由用户手动刷新，没有后台事件流与自动断线续传。
6. 当前 fingerprint 是客户端生成的随机 SHA-256 展示值，没有设备公钥持有证明；不能称为硬件级设备身份。
7. Android SDK/NDK/JDK 未安装，APK/AAB 未构建；macOS 未在 macOS 主机编译/签名。
8. 最新全仓 Python 3.11 回归已在所有本轮改动与共享修复之后完成：**2,356 tests / 0 failures / 0 errors / 1 skipped**。skill_fusion 的 ZIP mode/allowlist/SQLite cleanup 问题已修复；video admission 已补齐 resource evidence 与活动 GPU lease 的 ID/owner/coordination 绑定；两个专项分别 9/9 passed。当前没有已知 Python 测试失败。
9. Runner/PSMUX 有一项独立运行器问题：一次长时间 `run_process` 在 900 秒后没有 result marker，`muse_dev.py recover` 返回 `outcome_unknown/completion_not_yet_recorded`，active_job_id 仍占用。未强制清除该窗口；最终 Rust fmt/test/clippy/release 验证改由独立 `run_shell` 直接调用现有 D 盘 toolchain 完成。后续 `run_process` 可能仍被该 PSMUX 锁阻塞，需要单独恢复 Runner。

## 下一步

1. 将 Host TLS listener 生命周期纳入 Muse Supervisor/Commander，保留显式 opt-in 和默认 loopback；公网/隧道接入须有单独安全配置与验证。
2. 实现 Android Keystore / Apple Keychain 设备 token 与 claim secret 保存、删除、撤销和重新配对；可选增加证书 pinning。
3. 完成 Android SDK/NDK/JDK 规划与安装，构建 APK/AAB，并加入二维码/挑战码扫描与后台事件续传。
4. 配置真实可达 HTTPS Host（DNS/证书/防火墙或可信隧道），完成 Windows ↔ Android 跨网络端到端联调后，再考虑启用真实远程监听。
5. 恢复 Runner/PSMUX 的 outcome_unknown 活动窗口，避免后续 `run_process` 被锁；保留 direct run_shell 作为临时验证通道。
6. 补充 macOS runner 编译与签名验证。
