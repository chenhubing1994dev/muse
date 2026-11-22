# Muse 插件目录

本目录保存 Muse 插件的声明式清单与本地注册数据。插件清单是数据，不是可执行脚本。

- examples/：示例清单。
- data/plugins/registry.sqlite3：运行时注册数据库（由 muse-plugins 按需创建，不应提交到源码仓库）。
- 插件能力必须声明风险级别与权限，并包含 commander.enqueue。
- 清单变化会清除旧审批，必须重新审核。
- 插件依赖采用精确版本锁定；启用中的依赖不能被单独关闭。
- 插件请求通过 Commander TaskQueue 创建 waiting_approval Ticket，默认 execution_enabled=false。当前版本不加载或执行第三方插件代码。

不要把未审查的 Python、Shell、PowerShell、DLL 或二进制文件放入插件目录并期待 Muse 自动运行。未来执行适配器必须单独实现隔离、权限、资源配额、签名验证和人工审批。
