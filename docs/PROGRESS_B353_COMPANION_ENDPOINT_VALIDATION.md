# B353 — Companion 跨网络 Host 地址校验修复

日期：2026-11-22

## 本轮完成

- 修复 `ui-prototype/api.js` 的 Companion Host 地址过滤误判。
- 根因：旧逻辑用 `/^f[cd]/` 与 `/^fe[89ab]/` 对整个 hostname 做前缀匹配，把正常公网 DNS 名称（例如 `fc.example.com`、`fe80.example.com`）误当作 IPv6 ULA / link-local 地址。
- 改为解析 IPv6 字面地址的 8 组 16 位字段，再按地址位段判断：
  - IPv6 unspecified、loopback、ULA（fc00::/7）、link-local（fe80::/10）、multicast（ff00::/8）。
  - IPv4-mapped / IPv4-compatible IPv6 地址继续检查嵌入的 IPv4 地址。
  - IPv4 保留既有 loopback、RFC1918、link-local、CGNAT、multicast/reserved 拦截。
- DNS 名称按完整 hostname 规则检查；移除尾随根点后再判断 `localhost`、`.local`、`.lan`、`.home`、`.internal`，避免 `localhost.` / `muse.local.` 绕过本地名称拦截。
- HTTPS 强制、系统证书/主机名验证、禁止 userinfo / 非根路径、禁止 redirect、`credentials: omit`、`cache: no-store` 与设备 HMAC 签名边界均保持不变；不引入桌面 Bearer。

## TDD 与验证证据

- 新增回归测试后先观察到 RED：`https://fc.example.com` 被旧逻辑错误拒绝。
- 修复后 Companion 定向测试：**4/4 passed**。
- UI 全量 Node 测试：**66/66 passed**。
- `node --check api.js`：通过。
- `npm run check`：Svelte **0 errors / 0 warnings**。
- `npm run build`：Vite **149 modules transformed**，构建成功。

## 仍未完成 / 安全边界

- 这是对 IP 字面量与本地域名的客户端校验，不代表客户端能可靠验证 DNS 最终解析地址；DNS 解析到私网的风险仍需由可信 DNS、服务器访问策略与网络防火墙共同控制。
- 本轮没有配置真实公网 DNS、证书、防火墙或可信隧道；没有进行公网端到端连接。
- Android / iOS 真机、APK/AAB、Keychain / Android Keystore 尚未验收。
- Companion TLS Listener 仍由显式 CLI 启动，尚未纳入 Supervisor 生命周期；Companion 事件仍为签名 JSON 批次，SSE / 自动续传 UI 未完成。
- 不得把本轮自动化测试通过表述为公网部署或全平台实机验收完成。

## 后续优先级

1. 在不改变桌面 API loopback + Bearer 边界的前提下，将 Portal / Companion Listener 的显式启停、健康检查和证书到期提示纳入 Supervisor。
2. 为跨网络连接补齐可信 DNS / 反向代理 / 隧道部署验收与端到端测试。
3. 接入 Android Keystore / Apple Keychain，并完成真实设备配对、断线恢复和撤销测试。
4. 将灵感演化与研究进度改为 Commander 可追踪、可幂等、可取消/恢复的任务，不建立第二套调度队列。
