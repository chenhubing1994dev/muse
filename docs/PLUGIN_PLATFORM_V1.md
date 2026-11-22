# Muse 插件平台 v1

## 目标

以统一清单把本地能力、可信适配器与未来外部插件接入 Muse Commander。插件不能成为第二个调度中心，也不能绕过审批、GPU/资源门禁或审计。

## v1 已实现

- 严格 JSON manifest schema v1，限制 64 KiB、64 个 capability、32 个依赖。
- 插件 ID / capability ID 校验、SemVer 校验、权限白名单、重复 capability 拒绝。
- 清单 SHA-256 指纹；清单变化自动回到 discovered 并清除旧审批。
- 生命周期：discovered → approved → enabled → disabled；另预留 quarantined 状态。
- 依赖采用精确版本；启用依赖被其他启用插件引用时禁止关闭。
- SQLite 事务、审计事件与 manifest digest；关键操作在事务锁内完成。
- commander_request 只接受代码注入的可信 enqueue adapter，不导入、不 eval、不执行 manifest 指定的代码。
- commander_task_queue_adapter 将能力请求写入现有 Commander TaskQueue，初始状态固定为 waiting_approval，payload 标记 execution_enabled=false 与 execution_adapter_required=true，同一 payload 幂等。
- CLI：validate、discover、list、approve、enable、disable、request。
- Web 门户支持已登录会话通过 CSRF 保护提交严格 Manifest JSON；服务端仅登记为 discovered 待审核候选。已在本机独立批准并启用的插件可从网页提交能力请求，但请求只进入 Commander waiting_approval Ticket，不执行插件代码。

## Web Portal 插件 API

- `POST /api/v1/portal/plugins/discover`：已登录会话提交 `{manifest: {...}}`；严格 schema v1 校验后只登记 discovered 候选，响应明确 `review_required=true`、`execution_enabled=false`。
- `POST /api/v1/portal/plugins/{plugin_id}/capabilities/{capability_id}/request`：只接受 `{payload: {...}}`，且插件必须已由本机管理员批准并启用；通过既有 `commander_task_queue_adapter` 创建 waiting_approval Ticket。
- 两个写入口均要求精确 HTTPS Origin/Host、有效 Cookie session 与 CSRF header；拒绝重复 JSON key、非有限数、非对象 body、重复/缺失 Content-Length、Transfer-Encoding 和超限 body。
- 插件声明同步到 `CapabilityRegistry` 时固定为 `needs_approval`、`executable=null`、`execution_enabled=false`；启用插件也不会因此取得运行时权限。只有注册表计数稳定且列表覆盖全部插件时才清理已移除的能力；列表触及 500 项上限或读取期间计数变化时跳过 stale sweep，避免误禁用未读到的插件。
- 浏览器不能 approve、enable、disable 插件；对应路由返回 405。没有动态 import/eval、包安装或第三方代码执行。



## 清单示例

见 plugins/examples/research-cross-check/manifest.json。

字段：
- schema_version：当前为 1。
- id、name、version、description、publisher。
- dependencies：插件 ID 到精确 SemVer 的映射。
- capabilities：ID、描述、风险等级、权限清单。

当前权限 allowlist：research.read、knowledge.read、knowledge.write、commander.enqueue、filesystem.project_read、filesystem.project_write、network.public_research、gpu.request、video.render。这只是声明和审批信息，不等于已授予操作系统权限。

## CLI 示例（PowerShell）

~~~powershell
$env:PYTHONPATH = "src"
py -3.11 -m muse.plugin_platform validate plugins/examples/research-cross-check/manifest.json
py -3.11 -m muse.plugin_platform discover plugins/examples/research-cross-check/manifest.json --database data/plugins/registry.sqlite3
py -3.11 -m muse.plugin_platform approve research.cross-check --database data/plugins/registry.sqlite3 --approved-by owner
py -3.11 -m muse.plugin_platform enable research.cross-check --database data/plugins/registry.sqlite3 --enabled-by owner
~~~

如要创建 Commander 审批 Ticket，准备不超过 64 KiB 的 JSON payload：

~~~powershell
py -3.11 -m muse.plugin_platform request research.cross-check research payload.json --database data/plugins/registry.sqlite3 --task-db data/tasks/muse.sqlite3 --requested-by owner
~~~

Ticket 进入 waiting_approval。v1 没有插件执行 runtime；即使 Ticket 被批准，也不会加载第三方代码。必须等可信执行适配器实现并通过隔离/权限/资源测试后，才可考虑开放执行。

## 明确未完成

- 插件包签名、发布者信任根、签名轮换与撤销。
- 插件安装包下载、依赖解析器与供应链扫描。
- WASM/子进程沙箱、网络/文件/GPU capability broker。
- 插件健康探测、版本升级/回滚与运行时隔离。
- Commander Worker 对 plugin capability Ticket 的正式执行适配器。
- Web 端的插件审批/启用/停用交互仍刻意关闭；网页只登记候选，并为本机已启用插件提交 Commander 审批请求。插件审批/启停仍由受控 CLI/本机管理流程完成。


因此 v1 是安全的插件注册与审批底座，不是任意 GitHub 插件一键安装/执行器。
