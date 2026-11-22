# B351 — Muse 服务器网页门户与插件平台基础

日期：2026-11-22

## 本轮目标

在保持桌面 Status API loopback + Bearer 边界的前提下，增加一个可跨设备浏览的 HTTPS 服务器网页入口，并建立统一插件清单、权限、审批、依赖与 Commander 入队底座。

## 已落地

### HTTPS Server Web Portal
- 新增 src/muse/web_portal.py 与 CLI muse-web-portal。
- Portal 自己终止 TLS 1.2–1.3；必须提供现有证书/私钥；无 HTTP fallback。
- 默认 bind 127.0.0.1，只有操作者显式指定 bind 才会监听其他接口；origin 必须为精确 HTTPS 公网域名，拒绝 localhost、LAN/local hostname 与非全局 IP。
- Portal 内部启动原有 create_status_server，只在 loopback 使用随机 Bearer；浏览器不接触该 Bearer。
- 浏览器登录使用 MUSE_WEB_PASSWORD（16–1024 UTF-8 bytes，scrypt 验证）、5 次/15 分钟/IP 限速、8 小时 HttpOnly + Secure + SameSite=Strict Cookie、服务端 SHA-256 session/CSRF token 摘要、CSRF header、Origin/Host 校验；重启后清除旧 sessions。
- 仅开放显式 GET allowlist：overview、agents、inspirations、knowledge graph、system dashboard、plugins。Portal 不代理 POST/PUT/PATCH/DELETE 到 Status API。
- 上游 loopback fetch 禁止代理与 HTTP redirect，限制响应大小和超时。
- 静态文件路径限定在 web-portal 目录；CSP、HSTS、nosniff、no-referrer、frame denial、Permissions-Policy。
- 新增响应式赛博宇宙风格 PWA 页面：Commander 指标、事件、Agent、灵感、Research Wiki 知识节点、插件目录；补充 Android/桌面 PWA manifest 与 iOS 主屏幕 meta / SVG touch icon。Service Worker 只缓存静态壳，不缓存 API/个人数据。
- Web Portal 不改变桌面 Status API 监听策略；没有自动开放公网端口，没有信任 X-Forwarded-* 代理头。

### 插件超融合底座
- 新增 src/muse/plugin_platform.py、tests/test_plugin_platform.py、plugins/examples/research-cross-check/manifest.json。
- 严格 manifest schema v1、SemVer、ID、能力、权限 allowlist、依赖精确版本与 SHA-256 指纹。
- 状态：discovered / approved / enabled / disabled / quarantined；清单变更自动撤销旧审批。
- 依赖启停保护、SQLite 事务与审计。
- 插件清单不会动态 import/eval/执行任何代码。
- commander_task_queue_adapter 将能力请求提交到现有 TaskQueue，强制 waiting_approval、execution_enabled=false、execution_adapter_required=true，幂等键按插件指纹、能力和 payload digest 生成。
- CLI：validate / discover / list / approve / enable / disable / request；入口注册 muse-plugins。
- Web 插件面板读取同一注册数据库；当前只读。

## 验证

- Python 全仓：**2,375 tests / 0 failures / 0 errors / 1 skipped**（Python 3.11）。
- Web Portal + Plugin 平台专项：**18/18 passed**，含真实 TLS HTTPS 登录、Cookie 会话、CSRF 轮换/登出、只读代理、Commander Ticket 幂等、依赖保护与 manifest 审批重置。
- 全仓 compileall 通过。
- web-portal/app.js 与 web-portal/sw.js：Node syntax check 通过。
- muse-plugins 与 muse-web-portal CLI help 通过。
- 插件 CLI 全流程实测通过：validate → discover → approve → enable → Commander TaskQueue waiting_approval Ticket → list。
- Web HTTPS 集成测试验证知识图谱代理映射到 /api/v1/knowledge/graph?limit=80，浏览器请求不携带上游 Bearer。
- pyproject.toml TOML 解析通过，入口映射正确。
- 全仓测试末尾有一条 FFmpeg moov atom not found stderr 诊断输出，但测试进程退出码为 0，测试总结果仍为全绿；目前未将其归因为本轮代码错误。

## 使用方式

### Server Web Portal
需要用户自行配置公网 DNS 与受系统信任的 TLS 证书。PowerShell 示例：

~~~powershell
$env:PYTHONPATH = "src"
$env:MUSE_WEB_PASSWORD = "<请自行设置至少16字符的强密码>"
py -3.11 -m muse.web_portal --task-db data/tasks/muse.sqlite3 --progress-db data/progress/muse.sqlite3 --capability-db data/capabilities/muse.sqlite3 --session-db data/web-portal/sessions.sqlite3 --cert C:/path/to/fullchain.pem --key C:/path/to/private-key.pem --origin https://muse.example.com --static-dir web-portal --bind 127.0.0.1 --port 8443
~~~

默认只绑定 loopback。若要直接公网监听，必须由用户明确改为公网 bind，并自行配置 DNS、证书、主机防火墙与网络安全；不要使用 LAN 地址、HTTP、桌面 Bearer 或未加密代理。服务不信任 forwarded headers。示例数据库路径须按真实部署布局核对；本轮没有启动公网服务。

### Plugin Platform
详细流程与命令见 docs/PLUGIN_PLATFORM_V1.md；示例清单见 plugins/examples/research-cross-check/manifest.json。

## 明确未完成 / 不可宣称

1. Portal v1 是只读网页端；还没有通过 Web 端提交灵感、修改知识、批准任务、控制运行时或管理设备。
2. 当前只有单一共享密码，没有多用户、角色、SSO、MFA、审计登录主体管理或密码轮换界面。
3. Android/iPhone 可用浏览器访问响应式 PWA；原生 APK/AAB、iOS 包装、Keychain/Keystore 与原生推送仍未完成。
4. Portal 未配置用户实际公网域名/证书/防火墙，也未做外网部署或 Android/iOS/PC 多网络实测。
5. 插件 v1 只有声明式注册/审批/Ticket 入队；没有第三方包签名、下载安装、沙箱、运行时 capability broker、健康管理或执行 adapter。审批后的插件 Ticket 仍不能执行第三方代码。
6. macOS 构建/签名、Android SDK/NDK/JDK 与 MSI/NSIS 安装包仍未完成。
7. Runner/PSMUX 之前的 outcome_unknown 活动窗口尚未独立恢复；本轮验证经 run_shell 执行。

## 下一步

1. 在 Muse Commander/Supervisor 中管理 Portal/Companion TLS listener 生命周期、证书状态、启停和健康探测，仍保持显式 opt-in。
2. 给 Web Portal 增加真正的用户身份/RBAC/MFA，并将写操作转为 Commander 审批 Ticket；先只开放低风险提案写入。
3. 把灵感/研究/知识/反证/改进/审批/测试接成可追踪浏览器工作流。
4. 设计并实现签名插件包与可信发布者信任根；再评估 WASM/子进程隔离与资源 broker。
5. 让 Android/iOS/PC 共用同一安全 API、设备密钥库与事件续传；完成真实多设备端到端测试。
