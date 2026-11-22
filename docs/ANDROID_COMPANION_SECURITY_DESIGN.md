# Muse Android Companion — 安全与跨端架构

## 当前状态
- B348 已完成 Tauri 2 桌面 / Mobile Entry 拆分；Android Companion 未配对时不启动本机 Python，并明确拒绝访问。
- B349 已新增 Host 侧配对/设备凭据安全核心 `src/muse/companion_security.py`，并接入本机管理 API：短时单次挑战（同一 Host 仅一个活动挑战，新挑战事务性替换旧挑战）、失败锁定、Host 批准、一次性领取、只读权限、凭据哈希存储、撤销、HMAC 请求签名与 nonce 防重放。
- 桌面 Companion 管理 API 仍严格沿用现有 loopback + 桌面 Bearer 边界。B350 新增独立 TLS listener 与 Tauri WebView HTTPS 客户端，配对/领取及设备签名只读请求不使用桌面 Bearer；TLS listener 默认 loopback 且必须显式启动。
- 现有 Commander SSE 继续支持 `Last-Event-ID`、有限批次、过期游标 `muse-reset` 与敏感字段脱敏。B350 Companion `GET /api/v1/companion/events` 已提供签名 JSON 有限批次、持久事件 ID 与旧游标 reset；尚未实现 Companion SSE。
- Android SDK、NDK、JDK 当前开发机未安装，因此 APK/AAB 尚未构建或实机验收。旧 LAN 配对仍是 HTTP 方案，不能直接用于公网或不可信网络。
## 目标架构
Windows/macOS 桌面 Host 运行 Rust Supervisor 与本机 Python API；Android Companion 只连接用户明确配对的 Host。Host 内 Python 模块继续承担研究、记忆、视频与专业 Agent 能力，Commander 负责唯一的任务授权、调度、状态和审计。

## 配对与会话安全要求
1. Companion TLS transport adapter 已实现：TLS 1.2–1.3、证书/私钥强制加载、无代理头信任、有限握手并发与超时。Tauri WebView 客户端使用系统 CA 与主机名验证；证书 pinning 尚未实现。默认仅 loopback，跨网 DNS/防火墙/可信隧道仍未配置。
2. Host 侧核心已实现高熵、60–600 秒、单次使用配对挑战、失败次数锁定、待配对请求和 Host 批准状态；本机管理 API、桌面「设备安全」面板、独立 TLS listener 远程申请/领取路由及移动端 HTTPS 配对 UI 已接线。实际手机连接仍需可达的 Host HTTPS endpoint。
3. 核心已为每台设备签发独立随机凭据，只持久化 SHA-256 摘要，并支持设备撤销。移动端 claim secret 与 device token 目前仅保存在 App 内存；凭据轮换和 Keychain/Android Keystore 安全存储尚未实现。
4. 默认仅授予 `status.read`、`events.read`；核心只允许 GET 的 Companion 状态/事件路径，不授予任务提交、文件写入或执行控制权限。
5. HMAC-SHA256 请求签名绑定协议版本、设备 ID、会话 ID、nonce、毫秒时间戳、HTTP 方法、路径、请求体摘要和事件游标；服务端限制 60 秒时钟偏差并原子拒绝重复 nonce。签名密钥是设备凭据，传输必须受 TLS/可信隧道保护。
6. Companion 已复用 ProgressEventStore 的持久事件 ID，签名绑定 `Last-Event-ID`，并返回有限 JSON 事件批次与 reset 标记；Companion SSE 和移动端断线续传 UI 尚未实现。
7. 移动端任务提交、文件访问和执行控制暂不开放；未来每种能力都要独立授权，并只向 Commander 创建待审批请求，不得绕过其审批/资源门禁。
8. 核心审计配对请求、批准/拒绝、设备领取与撤销；不记录配对码、claim secret、设备 token 或敏感 payload。权限变更审计在权限管理落地后补齐。

## 当前实现边界
- `CompanionDeviceStore` 仍是纯安全领域层。独立 `CompanionTLSHTTPServer` 位于 `src/muse/companion_transport.py`，仅通过 `muse-companion-host` 显式启动；它不改变桌面 status API 的 loopback 限制。
- WebView CORS 只允许 Tauri app 精确 Origin 与路由所需 method/header；不使用通配符或 credential cookies。Tauri CSP 仅允许额外的 HTTPS connect-src。
- TLS adapter 只在实际 `ssl.SSLSocket` 且协商 TLS 1.2/1.3 后调用 `authenticate_request`；不会读取转发头。移动端必须验证 Host 证书/主机名或显式 pin，不能关闭证书校验。
- 当前 `device_fingerprint` 是客户端提交、供 Host 人工核对的 SHA-256 展示值；尚未完成设备公钥持有证明，不能将其描述为硬件/密钥级身份认证。
- Tauri Mobile Companion 已接入 WebView HTTPS + WebCrypto HMAC，可发起配对、领取只读凭据、读取状态和有限事件批次；若没有内存中的有效设备凭据则 fail-closed。凭据尚未持久化到 Keychain/Keystore，App 退出/重载需重新配对。
## Android 构建前置
- 安装兼容 Tauri 2 的 Rust toolchain 与 Android targets。
- 安装 JDK、Android SDK、platform-tools、NDK，并设置 JAVA_HOME、ANDROID_HOME/ANDROID_SDK_ROOT、NDK 环境。
- 配置 Android Studio/Gradle 与 Tauri mobile prerequisites。
- 检查生成目录后执行 cargo tauri android init，配置 application ID、签名与权限。
- 完成 Android debug build、设备配对、TLS 校验、断线续传、凭据撤销和权限负向测试。
- Release 签名、公证/商店发布需单独授权与验收。

## 平台状态
| 平台 | 当前可确认状态 | 尚缺证据 |
|---|---|---|
| Windows | Tauri Release EXE 9,024,000 bytes 已构建；Rust 9/9、Clippy 0 warnings；B350 Companion TLS 19/19；UI 60/60；Svelte check 0 errors/warnings；Vite 149 modules 成功；Python 全仓 2,356 tests / 0 failures / 0 errors / 1 skipped | Keychain/Keystore、Supervisor 生命周期、公网/隧道配置、Android 安装包与端到端手机连接 |
| macOS | Rust/Tauri 代码采用跨平台入口结构 | 未在 macOS 主机编译/签名；Keychain 凭据存储未实现 |
| Android | Tauri Mobile Entry、HTTPS 配对/只读 Companion UI 与 WebCrypto HMAC 客户端已实现（尚未实机运行；只接受公网 HTTPS 域名/可信隧道，不接受 LAN/loopback） | SDK/NDK/JDK、APK/AAB、Keystore、真实外网 Host endpoint 与实机测试 |
## 禁止事项
- 不修改桌面 Status API 的 loopback 绑定。Companion 若需非 loopback 监听，只能显式启动独立 TLS listener、提供可验证证书并通过设备 HMAC 认证；禁止任何明文 HTTP 公网监听。
- 不将本地桌面 API token、密钥或 Python bootstrap 输出透传给手机。
- 不因 Android 网络可达就宣称在线、已配对或允许执行。
- 不以手机端本地任务列表替代 Commander 的持久队列。
