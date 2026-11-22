# B349 — Muse Companion Host 安全核心与本机管理 API

日期：2026-11-22

## 本轮目标

在不开放公网端口、不改变现有 loopback API 安全边界、不复用桌面 Bearer token 的前提下，为后续 Host–Companion 连接建立可测试的配对、设备凭据、只读授权和请求防重放核心。

## 已落地

- 新增 `src/muse/companion_security.py`，使用 Python 标准库与 SQLite，不引入外部运行时依赖。
- 配对挑战：
  - 192-bit 随机一次性代码；只持久化 SHA-256 摘要。
  - 有效期限制为 60–600 秒，默认 180 秒。
  - 同一 Host 同时仅保留一个 open 挑战；生成新挑战会在同一 SQLite 事务中将旧 open 挑战置为 expired，并记录不含秘密的 superseded 审计事件。
  - 错误尝试计数与锁定；成功请求后挑战不可再次使用。
  - 设备配对请求包含客户端 claim secret 摘要，只有 Host 批准后才可领取凭据。
- Host 审批：
  - 待配对设备可列出并显示名称与 fingerprint。
  - Host 显式批准或拒绝；未批准时无法领取设备凭据。
  - claim secret 只存摘要；设备凭据只返回一次。
- 设备凭据：
  - 每台设备独立随机 256-bit token；数据库仅存 SHA-256 摘要，不保存原始 token。
  - 默认权限固定为 `status.read`、`events.read`；无任务提交、文件写入或执行控制权限。
  - 支持设备列表、状态查询和即时撤销。
- 请求认证：
  - HMAC-SHA256 签名绑定协议版本、设备 ID、会话 ID、nonce、时间戳、方法、路径、请求体摘要和事件游标。
  - 时间窗为 ±60 秒；SQLite 事务原子消费 nonce，拒绝重放。
  - 只允许两个明确 GET 路径：`/api/v1/companion/status` 与 `/api/v1/companion/events`。
  - 必须由传输适配层在验证 TLS/可信隧道后才可调用；仅有转发头不构成传输证明。
- 审计记录配对挑战、配对请求、批准/拒绝、领取和撤销；不写入配对码、claim secret、原始 token 或敏感 payload。
- 将领域核心接入现有 Host `status_api.py` 的本机管理 API：生成配对挑战、列出待配对请求/设备/审计、批准/拒绝、撤销设备；这些路由继续受 loopback + 桌面 Bearer 保护。
- 新增 `ui-prototype/src/lib/CompanionPanel.svelte` 和「设备安全」导航页；可生成/复制一次性 Host 挑战、查看待审批设备/已登记设备/审计记录，并批准/拒绝/撤销。页面明确标注远程申请通道未开放，不会假报手机已连接。
- 扩展 API 客户端及类型定义，加入本机 Companion 管理调用，并为其添加客户端测试。
- 更新 `docs/ANDROID_COMPANION_SECURITY_DESIGN.md`，区分已经实现的领域核心/本机 API/UI 和仍未接通的传输/移动端部分。

## 验证

- Companion 安全核心专项：**10 tests / 0 failures / 0 errors**。
- Companion + Status API 回归：**57 tests / 0 failures / 0 errors**（包含本机管理 API 完整配对/撤销与新挑战原子替换测试）。
- Python 3.11 全仓回归：**2,333 tests / 0 failures / 0 errors / 1 skipped**；`compileall` 通过。
- UI API + Svelte：**57 TAP tests / 0 failures**；`svelte-check` 0 errors / 0 warnings；Vite production build 147 modules transformed，成功。
- 测试覆盖：未批准不可领取、一次性领取、错误码锁定、过期挑战、输入校验、token 不以明文入库、HMAC 请求绑定、游标篡改、请求体/路径篡改、时间窗、重复 nonce、拒绝 POST、传输未验证 fail-closed、设备撤销、审计不泄密。
- 本轮全量 Python 回归已重跑；2,333 项结果包含本轮新增 Companion 安全、Host 管理 API 与单活动挑战测试。

## 尚未完成（不得宣称已交付）

1. Host 本机配对管理 API 与桌面设备安全 UI 已接线；但尚无远程设备申请/领取接口，手机端不能完成配对。
2. Companion TLS transport adapter 尚未实现；没有 HTTPS listener，也没有验证用户配置的可信隧道。
3. 当前 status API 仍只绑定 loopback 并要求桌面 Bearer；没有将设备凭据接入现有 HTTP/SSE handler。
4. 移动端 Host 地址配置、二维码/配对码录入、claim secret 生成和安全存储尚未实现。
5. Android Keystore / macOS Keychain / Windows Credential Manager 的平台凭据保护尚未实现。
6. 当前 fingerprint 是客户端提交、供 Host 人工核对的展示值；尚无设备公钥持有证明，不能称为硬件级设备身份。
7. Companion 状态/SSE 路由尚未接入现有 Commander 数据与事件游标；任务提交/执行控制保持关闭。
8. Android SDK/NDK/JDK 未安装，APK/AAB 未构建；macOS 未在 macOS 主机编译或签名。

## 下一步

1. 独立实现并验证 Companion HTTPS transport adapter；TLS 主机名/证书校验或明确配置的可信隧道必须可验证，不能信任任意转发头。
2. 在已验证传输上增加远程配对申请/领取接口，并接入现有 Host 批准 UI；设备凭据仍只授予只读权限。
3. 将设备认证适配器接入只读 status 与 Commander SSE，签名必须覆盖 Last-Event-ID；保留现有脱敏和 reset 语义。
4. 实现移动端 Host 地址配置、claim secret 生成、Keychain/Android Keystore 凭据安全存储与撤销/重配流程，再做跨网络端到端联调。
5. 完成后重跑全仓 Python、Rust、Svelte、Tauri 目标构建与安全负向测试。
