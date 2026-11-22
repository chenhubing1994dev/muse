# B352 — Server Web Portal 插件能力请求闭环与 PWA 跨端图标

日期：2026-11-22

## 本轮落地

- HTTPS Web Portal 增加插件 Manifest 候选登记入口：POST /api/v1/portal/plugins/discover。仅接受 schema v1 manifest，登记后保持 discovered，响应明确标记需要审核且不执行。
- 已由本机管理员独立批准并启用的插件，可从网页提交能力请求：POST /api/v1/portal/plugins/{plugin_id}/capabilities/{capability_id}/request。请求通过现有 commander_task_queue_adapter 写入 Commander TaskQueue，状态为 waiting_approval。
- 网页端没有 approve/enable/disable 操作；未启用插件不能提交能力请求。即使插件已启用，CapabilityRegistry 条目仍是 needs_approval、executable=null、execution_enabled=false。
- 写入口要求 HTTPS、精确 Origin/Host、登录 Cookie session 与 CSRF；严格检查 Content-Type、单一十进制 Content-Length、请求体大小、重复 JSON key、非有限数和 JSON 对象形状。Manifest 本身仍受 64 KiB schema 限制。
- 浏览器表单只做 JSON 对象形状预检，但通过 rawBody 原样发送 JSON envelope，避免 JSON.parse/stringify 抹掉重复键，让服务端严格解析器仍能拒绝重复 key。
- Web PluginHub 增加候选登记表单及已启用能力请求表单；成功请求显示 Commander task ID，页面不执行插件代码。
- PWA 增加无第三方依赖的 PNG 图标生成器，产出 iOS 180×180 Apple Touch Icon、Android/桌面 192×192 与 512×512 图标；manifest、HTML apple-touch-icon 与 Service Worker shell cache 同步更新。
- Web Portal 数据库路径要求插件、任务、能力数据库为不同的普通文件且不能是符号链接；静态根目录拒绝符号链接。CapabilityRegistry 在插件列表读取/候选登记时进行声明元数据同步；只有注册表计数稳定且本次列表覆盖全部插件时才清理旧能力，超过 500 条或快照变化时 fail-safe 跳过 stale sweep。
- 新增受保护的 `GET /api/v1/portal/tasks` Commander 任务账本；只返回最近 40 项任务的安全元数据，不暴露 payload、lease 或 Worker 内部字段；网页只读展示，不改变 Commander 调度权，接口故障时仅任务面板局部降级。
- Portal 管理员凭证现持久化为 Session SQLite 中独立随机盐 + scrypt 摘要，不保存明文；后续启动必须匹配存储凭证，否则 fail closed。新增无回显 `muse.web_portal_admin` 密码轮换命令，要求当前密码并撤销所有浏览器 Session。
## 验证结果

- Web Portal + Plugin Platform 专项回归：**35/35 passed**（70.038 秒）；覆盖 Portal 持久化 scrypt 管理员凭证/密码轮换、TLS 登录、Cookie/CSRF、插件 Manifest、Ed25519 信任根/签名包完整性/撤销、Commander 入队、灵感闭环与任务账本脱敏。
- 最新全仓 Python 回归：**2,398 tests / 0 failures / 0 errors / 0 skipped**（336.476 秒）；`scripts/validate_full_suite.py` Job `wc_job_R5C9gPKXTb0KGhmn` exit 0。前端 Node 历史验证：**66/66 passed**（`node --test`，ui-prototype）；`web-portal/app.js`、`sw.js` Node syntax check 通过；PWA manifest JSON 与 CSS 结构检查通过。
- PNG 专项测试验证三个图标的签名、尺寸、每个 PNG chunk CRC 与 IDAT 解压长度。
- PWA 图标生成脚本经 Muse Dev / PSMUX 执行成功，三个 PNG 均已生成。

## 安全边界与未完成事项

- 插件发现、能力声明和网页请求不代表授予执行权限；当前仍无任意第三方插件 runtime。
- 已新增 Ed25519 发布者信任根与签名 bundle 验证：可固定/撤销发布者公钥、验证 canonical manifest + artifact SHA-256 的域分离签名、拒绝未知/撤销/发布者不匹配的密钥与被篡改包，并将签名记录绑定插件版本和 manifest digest。相同版本与 manifest 不允许替换不同 artifact；撤销密钥会使其已验证签名记录转为 revoked。
- 签名验证只做供应链身份/完整性检查：不解压、不执行、不启用插件，也不绕过人工审批。WASM/受限子进程沙箱、资源配额、运行时健康检查、版本回滚和 Commander Worker 正式执行适配器仍未完成。
- 安装签名验证依赖：`pip install -e '.[plugin-security]'`。管理员以 raw 32-byte Ed25519 公钥导入信任根：`muse-plugins trust-key <key-id> --publisher <publisher> --public-key-file <public-key.bin> --database <plugin-db> --trusted-by <operator>`；验证候选包：`muse-plugins discover-signed <manifest.json> <artifact-file> --key-id <key-id> --signature-file <signature.bin> --database <plugin-db>`（签名文件为 raw 64-byte Ed25519 signature）；撤销：`muse-plugins revoke-key <key-id> --database <plugin-db> --revoked-by <operator>`。签名者必须按 `plugin_signature_payload(manifest, artifact_sha256)` 生成签名；单个 artifact 上限 256 MiB。

- Android、iPhone、iPad、macOS、Windows 的真实设备/浏览器验收尚未执行；当前完成的是响应式网页与 PWA 资源适配，不宣称实机全兼容。
- 用户域名、TLS 证书、公网服务器部署和外网验收尚未执行；未发布公网。
- 改动写入当前绑定的 Muse 项目工作区；未部署公网，未执行项目迁移或合并操作。项目不是 Git 仓库，使用逐文件读取、版本校验与自动化测试验证。
