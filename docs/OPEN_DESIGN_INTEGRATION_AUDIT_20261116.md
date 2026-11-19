# Open Design 本机安装与 Muse 融合审计

日期：2026-11-16

## 审计范围与结论

本轮只读审计本机安装、用户数据位置、CLI/MCP 入口和 Muse 现有适配能力；没有升级、重启、启动 Open Design daemon、修改 Codex/MCP 配置或写入 Open Design 用户数据。

- 本机安装版本：**0.25.0**（Windows 注册表 DisplayVersion、resources/app/package.json version、open-design-config appVersion 三处一致）。
- 安装目录：`D:\Programs\Open Design`。
- 用户数据根目录：`%APPDATA%\Open Design`；稳定命名空间：`namespaces\release-stable-win`。
- 数据区约 78 MB；其中 user-data 约 68.3 MB，项目目录当前有 2 个条目；SQLite 主库约 4.55 MB，另有约 4.51 MB WAL。升级前必须关闭应用并对整个 Open Design AppData 根目录做一致性备份，不能只复制主库而遗漏 WAL。
- 安装目录约 1.305 GB。未发现 Open Design.exe 进程正在运行。
- 已确认安装包内有 Node daemon CLI：`resources/app/prebundled/daemon/daemon-cli.mjs`；`node <entry> --help` 与 `node <entry> mcp --help` 均成功退出。只运行了帮助命令，没有启动 daemon。
- 没有发现 Muse 源码/文档中已有 Open Design 专用接入；但 Muse 已有通用 `MCPStdioClient`（`src/muse/software_crafting.py`），可复用其 stdio JSON-RPC 握手、固定 argv、shell=False、超时/输入输出上限、工具静态 allowlist、只读/写入批准分离和子进程环境脱敏。不得再造 MCP client 或第二套任务队列。

## 官方版本核验

截至本审计日，官方 GitHub Releases 将 **open-design-v0.27.1** 标记为 Latest，发布日期 2026-11-10，release commit `b83646f`。

相关版本能力：
- 0.26.1：Skill Discovery 按交付物类型选择原生 Skills，覆盖 11 类交付物，包括交互原型和 Motion；另有 CLI/BYOK 与 Motion 导出改进。
- 0.27.0：团队共享工作区/额度；支持以参考视频驱动 Motion 创作；新增 13 个 Motion 模板；统一规划与制作过程展示。
- 0.27.1：主要是邀请奖励/订阅活动和版本更新；实际融合价值主要来自 0.26.1 与 0.27.0 的能力。

官方来源：
- https://github.com/nexu-io/open-design/releases
- https://github.com/nexu-io/open-design
- https://github.com/nexu-io/open-design/README.md

不要把开发分支或 prerelease 当成稳定版。当前已安装 0.25.0，落后于官方稳定版 0.27.1。

## 本机 CLI / MCP 发现

- CLI help 显示 `od mcp` 会运行 stdio MCP server；已安装包的 MCP CLI 说明它可在 daemon 停止时启动签名 packaged app 的 headless runtime，并在 daemon 端口变化后重新发现运行时。
- help 摘要列出项目/文件查询与 `create_artifact` 等基础工具。
- 对已安装的 bundle 做静态只读检查，还发现内部工具定义包含 `create_project`、`list_skills`、`list_plugins`、`list_agents`、`start_run`、`get_run`、`cancel_run` 等；`start_run` 的说明是由 Open Design 自己启动其 agent，并返回 runId，随后通过 `get_run` 查询终态。静态 bundle 不是运行时 tools/list 证据；必须在获准启动本机 daemon 后做真实 MCP 握手，确认实际暴露的工具及 schema。
- CLI help 与 bundle 静态工具定义存在范围差异，不能仅凭帮助摘要硬编码工具表。

## 已知写入风险

官方 issue #8832 曾报告 Open Design 0.25.0 的 MCP `create_artifact` / `write_file` 偶发无响应，而只读调用正常；报告者在同一版本中观察到 `start_run` 可正常返回并完成。该问题在本次检索结果中仍标为 Open，尚不能据此断言 0.27.1 也有或没有此问题。

因此：
1. 升级并做真实 MCP tools/list 前，不把 0.25.0 的写工具作为生产写入通道。
2. 升级后必须对 create_project、start_run、get_run、create_artifact/write_file 做有界 smoke test；写操作设置硬超时，超时后先检查项目文件/运行状态，禁止盲目重发。
3. 生成优先使用 `start_run` 工作流，直接文件写入只作为经验证的辅助路径。
4. 不允许删除项目/文件工具进入默认 allowlist。

参考：https://github.com/nexu-io/open-design/issues/8832

## Muse 融合架构决定

Open Design 是视觉/创作执行引擎，不是 Muse 的大脑：
- Muse Commander 仍是唯一任务调度中心、TaskQueue/审批/状态账本和结果汇总者。
- Open Design 的 `start_run` 是一个受控子执行；必须由 Muse 先创建/领取一个有幂等键的父任务，再传入固定项目、明确 brief、明确 skill/plugin 和明确 agent。
- 不允许省略 agent 后静默使用 Open Design 默认 Cloud/AMR runtime。先用只读 `list_agents` 发现本机可用 agent；只有显式选定并核验的本地 runtime 才进入默认路线。Cloud、BYOK 外部 endpoint 和收费服务均需单独显式配置/授权。
- Muse 负责研究证据、反证、来源与结论；Open Design 负责把已审阅的内容渲染为研究专题网页、知识展示页、仪表板、网站/App 原型、演示文稿、图文刊物和 Motion 视觉稿。
- 输出文件、preview URL、运行 ID、源文件 SHA-256、导出文件和 QA 结果回写 Muse 现有 Creator Initiative / Research Wiki / 产品账本；不建立第二份知识图谱或第二个 scheduler。
- Open Design 的 MP4/Motion 可作为视觉素材与动效执行路径；最终视频仍须经过 Muse 的 VideoProductBuilder、旁白/音频处理、FFmpeg 解码验证、产品 ZIP 与发布门禁。不得把 Open Design 的 preview 成功等同于真实 MP4 发布验收。

## 分阶段实施与验收

### A. 安全升级准备
- 记录安装包版本、签名者、SHA-256 和下载来源。
- 关闭应用后备份整个 `%APPDATA%\Open Design`，并保留 0.25.0 安装回滚路径。
- 只下载官方 Windows x64 稳定安装包；校验后再升级。升级前不删除旧数据或手工改 SQLite。
- 升级后核对注册表、package.json、open-design-config 三处版本一致；确认项目数、用户数据目录、DB integrity 和启动/关闭正常。

### B. 只读连接
- 使用 Muse 现有 `MCPStdioClient`，绝对路径启动已签名的 packaged daemon CLI；不写 Codex/其他 agent 的全局 MCP 配置。
- 初期 allowlist 只含 `list_projects`、`get_active_context`、`get_project`、`list_skills`、`list_plugins`、`list_agents`、`get_run`、`list_files`、`get_file`、`get_artifact` 等实际存在且经 tools/list 确认的只读工具。
- 所有工具名、schema、annotation 必须运行时发现并再次匹配静态 allowlist；工具不存在或 annotation 不明确时 fail-closed。

### C. 受控创作
- 创建唯一的 Muse Creative Studio 项目；只创建一次并持久保存 Open Design project ID。
- 在 Muse 的既有 TaskQueue 中建立 parent task；`create_project`、`start_run` 及任何写文件工具都要走明确的审批和幂等检查。
- 仅使用明确选定的本地 agent；运行中以 `get_run` 轮询并同步父任务进度；终态后读取 artifact、验证路径/哈希并登记到 Muse 账本。
- 超时、daemon 断线、部分文件写入或 run 状态不明时先核查再恢复，不自动重试非幂等操作。

### D. 视觉路由
- Research Studio：研究专题、来源引用、支持/反证图谱。
- Web & App Studio：网站、应用、产品原型。
- Data Studio：数据仪表板和决策室。
- Presentation Studio：PPT/PDF/电子刊物。
- Motion Studio：品牌动效、信息动画、视频片头/转场；与 Wan/ComfyUI 真实镜头和 Muse 视频流水线协作。
- 每类路由必须声明输入证据、目标交付物、输出文件、QA 门槛和失败回退；不允许仅凭“生成成功”标记 ready_to_publish。

## 本轮未做事项

- 没有升级 Open Design。
- 没有启动 daemon、MCP server 或 Open Design 主程序。
- 没有读取/导出用户项目内容或凭证。
- 没有创建 Open Design 项目、调用生成、写入文件或修改全局 MCP 配置。
- 没有修改 Muse 业务代码，也没有运行 Open Design live MCP smoke test。

下一步：用户确认允许执行安全升级后，先备份 AppData 并下载/校验官方 0.27.1 Windows x64 安装包；升级后再进行真实 tools/list 与本地 agent 可用性验证，之后实现 Muse 的 Open Design capability adapter。
