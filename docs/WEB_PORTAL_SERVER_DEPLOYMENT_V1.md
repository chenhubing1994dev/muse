# Muse Server Web Portal 部署指南（v1）

> 当前状态：Portal 服务端与 PWA 代码已落地并通过自动化测试；真实公网 DNS、证书、防火墙和移动设备验收尚未完成。本指南不代表已经替用户部署服务器。

## 1. 部署边界

- Portal 进程本身提供 HTTPS（TLS 1.2–1.3），不提供 HTTP 降级。
- 默认只绑定 `127.0.0.1`。只有明确配置 `--bind` 才会监听其他接口。
- `--origin` 必须是精确的公网 HTTPS Origin，例如 `https://muse.example.com`；不接受 localhost、LAN 地址或本地域名。
- `GET /healthz` 提供不含敏感信息的 HTTPS liveness 检查；`GET /readyz` 额外以只读方式检查 Session、Plugin、Task、Capability SQLite 可访问性，并通过受保护的本机 Status API `/api/v1/overview` 做 readiness 探测。依赖缺失/不可用时 `/readyz` 返回 503，响应不泄露具体数据库路径或内部错误。
- Portal 在服务器内部启动既有 Status API，使用 loopback 随机 Bearer。该 Token 只在服务器进程间使用，不得复制到浏览器、PWA 或手机。
- 不要把 SQLite 数据库、私钥或环境变量放入 `web-portal/` 静态目录。
- 当前仍是单一管理员访问密码，不是多用户服务；首次启动由部署者通过 `MUSE_WEB_PASSWORD` 从服务器环境安全注入。可在受信任的本机交互式终端运行 `PYTHONPATH=src py -3.11 -m muse.web_portal_admin --generate-initial-password`（Windows PowerShell 先设置 `$env:PYTHONPATH = 'src'`，再运行 `py -3.11 -m muse.web_portal_admin --generate-initial-password`），生成 48 字节 CSPRNG / 384-bit URL-safe 随机口令。该命令会将口令打印到当前终端，**不要通过 psmux、CI、日志捕获或命令重定向运行**；请立即存入密码管理器，再通过服务管理器/受保护 EnvironmentFile 注入。Portal 会在 Session SQLite 中持久化独立随机盐与 scrypt 摘要（不保存明文），后续启动时必须提供匹配的 `MUSE_WEB_PASSWORD`；不匹配会拒绝启动。旧版 Session DB 首次升级时会以当前环境密码建立该凭证记录。
- 修改密码前先停止 Portal 服务，再运行 `PYTHONPATH=src py -3.11 -m muse.web_portal_admin --session-db data/web-portal/sessions.sqlite3`（Windows PowerShell 可先设置 `$env:PYTHONPATH = 'src'`，再运行 `py -3.11 -m muse.web_portal_admin --session-db data/web-portal/sessions.sqlite3`）。工具会无回显地提示输入当前密码、新密码及确认；成功后必须同步更新服务环境中的 `MUSE_WEB_PASSWORD` 再启动。密码轮换会撤销全部旧浏览器 Session。不要把口令写入脚本、命令历史或版本库。

## 2. 服务器准备

1. 选择一台运行 Muse Python 环境的服务器；本轮只在 Windows / Python 3.11 环境完成自动化验证，Linux/macOS 仍需单独验收。
2. 为服务器配置公网域名，并让 DNS 指向服务器。
3. 准备受信任的 TLS 证书和私钥；保护私钥文件 ACL。
4. 确认以下数据库路径指向真实 Muse 数据库，而不是新建的空数据库：
   - TaskQueue：`data/tasks/muse.sqlite3`
   - Progress：`data/progress/muse.sqlite3`
   - Capability：`data/capabilities/muse.sqlite3`
   - Plugin registry：`data/plugins/registry.sqlite3`
   - Portal sessions：`data/web-portal/sessions.sqlite3`
5. 设置强密码环境变量 `MUSE_WEB_PASSWORD`（至少 32 个 UTF-8 字节），并将 `PYTHONPATH` 指向 `src`。
6. Portal 会从项目 `data/` 目录自动发现已存在的 Agent、Improvement、Knowledge、Reports、Products、GPU Resource 等可选数据源；缺失项保持未配置，不会创建空数据库。`--task-db` 必须指向已存在的 TaskQueue 数据库。

## Linux systemd service template

- 可使用 `deploy/systemd/muse-web-portal.service` 作为 Linux systemd 服务模板。服务以专用 `muse` 用户运行，默认只监听 `127.0.0.1:8443`，启用 `NoNewPrivileges`、`ProtectSystem=strict`、私有临时目录、严格文件权限和内核保护；Portal Session 数据库放在 systemd 管理的 `/var/lib/muse-web-portal/`。
- 模板启动前会检查真实 TaskQueue、Progress、Capability、Plugin registry 数据库及 TLS 证书/私钥均可读；不会自动创建空业务数据库。请按实际安装路径修改模板中的 `/opt/muse`、域名与证书路径。
- 创建 `muse` 系统用户/组，将项目与 Python 3.11 环境部署到 `/opt/muse`、虚拟环境放在 `/opt/muse/.venv`，并确保 `data/` 中的真实数据库可由 `muse` 用户读写。证书放在 `/etc/muse/tls/`，私钥仅授予服务所需读取权限。
- 将 `deploy/systemd/muse-web-portal.env.example` 复制为 `/etc/muse/web-portal.env`，将 `MUSE_WEB_PASSWORD` 替换为至少 32 位随机字母数字口令；文件应归 `root:root` 所有并执行 `chmod 600`。不要将真实 env 文件放进项目目录或版本库。
- 将 unit 复制到 `/etc/systemd/system/muse-web-portal.service`，执行 `systemctl daemon-reload`、`systemctl enable --now muse-web-portal`，再用 `systemctl status muse-web-portal` 检查状态。默认 loopback 仅供本机/受控反向代理；直接公网监听必须由管理员显式把 `--bind 127.0.0.1` 改为 `--bind 0.0.0.0` 并配置仅开放所需端口的防火墙。
- 本仓库只对 unit 的静态安全约束做自动化测试；尚未在 Linux 主机运行 `systemd-analyze verify` 或启动服务。部署前应执行 `systemd-analyze verify /etc/systemd/system/muse-web-portal.service`，再完成真实 TLS、Host/Origin、反向代理与 `/healthz` 验收。

## 3. PowerShell 启动示例

以下路径仅为示例。运行前先核对它们与服务器上的真实数据布局；不要把示例数据库误当作现有生产库。

~~~powershell
$env:PYTHONPATH = "src"
$env:MUSE_WEB_PASSWORD = "<自行设置至少32字节的高熵密码>"

py -3.11 -m muse.web_portal `
  --task-db data/tasks/muse.sqlite3 `
  --progress-db data/progress/muse.sqlite3 `
  --capability-db data/capabilities/muse.sqlite3 `
  --plugin-db data/plugins/registry.sqlite3 `
  --session-db data/web-portal/sessions.sqlite3 `
  --cert C:/secure/fullchain.pem `
  --key C:/secure/private-key.pem `
  --origin https://muse.example.com `
  --static-dir web-portal `
  --bind 127.0.0.1 `
  --port 8443
~~~

该配置只监听本机回环地址，适合本机验证或配合受控的 HTTPS-to-HTTPS 反向代理。若要直接从公网访问，必须由服务器管理员明确改为公网监听地址（例如 `--bind 0.0.0.0`），并自行配置主机防火墙，只开放所需 HTTPS 端口。

## 4. TLS / 反向代理注意事项

- Portal 会验证 TLS、Host 与 Origin；不会信任 `X-Forwarded-Proto`、`X-Forwarded-Host` 等转发头。
- 不要使用“公网 HTTPS → 内网明文 HTTP → Portal”的配置；Portal 会拒绝该连接。
- 若使用反向代理，代理到 Portal 的上游连接也必须使用 HTTPS，并保留浏览器请求的正确 Host/Origin；证书验证和代理配置需单独验收。
- 如果不确定代理配置，优先让 Portal 自己终止 HTTPS，并使用显式公网 bind 与防火墙规则。

## 5. 网页功能与审批边界

- 浏览器可以读取 Commander 状态、Agent、灵感、知识图谱和插件目录。
- 灵感捕获默认只使用本地知识；只有明确选择“允许本条灵感进行外部联网检索”才会保存该条目的外部研究授权。
- 启动灵感演化时，网页只传 `inspiration_id`；不会启动视频任务或自动执行代码修改。演化产生的改进提案仍需人工审核。
- 插件 Manifest 只登记候选；插件能力请求进入 Commander 待审批队列。
- 网页不能批准或启用插件，也不执行第三方插件代码。

## 6. PWA 与跨设备

- Android：可用 Chrome 打开域名后安装/添加到主屏幕。
- iPhone/iPad：可用 Safari 的“添加到主屏幕”。
- PC：可用支持 PWA 的桌面浏览器安装或直接访问。
- 本轮只完成自动化资源与 Service Worker 测试；真实 Android、iOS、Windows/macOS 浏览器安装与网络切换仍需验收。
- 离线只保留静态应用壳；API、登录状态与知识数据不缓存，离线时不能继续研究或提交任务。

## 6.1 反向代理与可信边界

- Portal 自身只接受 HTTPS；若使用 Nginx/Caddy 等反向代理，应采用到 Portal 的 HTTPS 上游再加密并验证上游证书，不能把明文 HTTP 转发到 Portal TLS 监听端口。
- 启动时必须提供真实公网 `--origin https://your-domain.example`；Host 与 POST Origin 必须精确匹配该 origin。Portal 启动 JSON 会回报已验证的 `configured_public_url`；未配置真实域名/证书前，不应声称公网部署完成。
- 默认不信任任何转发来源标头。只有显式配置 `--trusted-proxy-cidr 127.0.0.1/32`（可重复，最多 16 个；IPv4 最宽 `/16`、IPv6 最宽 `/48`）后，且 TCP 直接对端确实属于该网络，Portal 才会接受恰好一个 `X-Forwarded-For` 标头中的单一 IP，仅用于登录限速与审计来源。优先使用代理单 IP `/32` 或 `/128`，不要信任宽泛网段。
- 受信代理必须覆盖写入 `X-Forwarded-For` 为其实际观察到的客户端地址；重复标头、逗号分隔链、非法 IP 均退回代理 TCP 地址。Nginx 应使用 `proxy_set_header X-Forwarded-For $remote_addr;`，不要使用 `$proxy_add_x_forwarded_for`。`Forwarded`、`X-Real-IP`、`X-Forwarded-Proto`、`X-Forwarded-Host` 永远不会被用来证明 HTTPS 或覆盖 Host/Origin。
- 代理必须原样保留浏览器 Host/Origin，并以 HTTPS 连接 Portal 上游、验证上游证书；不得采用公网 HTTPS → 内网明文 HTTP → Portal。Portal 本身始终执行 TLS 握手与 Host/Origin 校验。
- 防火墙应阻止公网直接访问 Portal 上游监听端口，只允许受控反向代理主机连接；不要把 loopback 上游 Status API 或 Bearer Token 暴露给代理外部。
- `/healthz` 只表示进程存活；部署监控应使用 `/readyz` 判断依赖可用性。两者都不得配置为绕过 TLS 或身份边界的公网管理入口。
## 7. 维护与安全

- 定期备份 TaskQueue、Progress、Capability、Plugin registry、Knowledge 数据库及 Portal session 数据；备份文件放在静态目录之外。
- 不要把密码、Token、私钥写进命令历史、日志或网页源码。
- Portal 重启会使旧浏览器 Session 失效，需要重新登录。登录失败次数与登录审计事件保存在 Portal session SQLite 中：同一来源地址 15 分钟内连续 5 次失败会被限速；成功登录会重置该地址的失败计数。审计只记录来源地址、事件类型和时间，不记录密码或 Token；事件最多保留 7 天且上限 10,000 条。
- SQLite 数据库处于 WAL 模式时，不要只复制主 `.sqlite3` 文件作为备份。新增 `muse-portal-db backup --source-db <db1> --source-db <db2> --backup-dir <outside-static-dir>`：通过 SQLite Online Backup API 获取一致快照、执行 `PRAGMA quick_check` 并生成含 SHA-256 的 manifest。恢复命令 `muse-portal-db restore --backup-file <backup.sqlite3> --destination <new-path.sqlite3> --expected-sha256 <manifest-sha256>` 会先校验哈希，再写入新路径、拒绝覆盖既有文件并再次执行完整性检查。恢复前停止 Portal 与相关写入服务；先恢复到新路径验证，再由管理员切换正式数据库。备份目录必须放在静态网页目录之外，并按服务器操作系统权限保护。
- 目前没有多用户隔离、MFA、SSO 或设备级原生密钥库；不要把它当作多人 SaaS 部署。
- 目前没有 Supervisor 生命周期管理、证书自动续期、原生推送或后台同步。
