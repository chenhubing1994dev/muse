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

## 2026-11-21｜Muse 适配层继续融合（静态实现，未做 live 连接）

本轮核实上游 MCP 的真实分层后，修正了 Muse 适配器，不再把资源端点误当作工具：
- 上游 `apps/daemon/src/mcp.ts` 的 `list_skills` / `list_plugins` 是只读发现工具；设计系统通过 MCP Resources 暴露为 `od://design-systems/<id>/DESIGN.md`，Skill 说明通过 `od://skills/<id>/SKILL.md` 暴露。 `start_run` 是 OpenDesign 自己执行 recipe 的入口，Muse 不应在本地重复实现其渲染器。
- Muse `OpenDesignAdapter` 新增 `list_skills()`、`list_plugins()`，并由 `doctor` 返回真实目录与数量；对目录结构、条目类型、ID 长度与重复 ID 做 fail-closed 校验。
- 入队前核对 skill/plugin ID 是否确实存在；获得 Muse 人工批准后、真正执行 `start_run` 前再次核对，防止目录变化或已卸载配方在审批后被错误执行。
- 只读 MCP 请求改成只执行目标工具，不再附带无关的 `get_active_context` / `list_projects` 调用；同时确认目标工具存在于运行时目录。
- 安全连接默认先检查 loopback `/api/health`，健康检查失败就终止在 MCP 子进程启动之前；不会因为启动 `od mcp` 而让 OpenDesign CLI 隐式拉起 headless daemon。Muse 主动启动仍受 `auto_start_daemon` 的显式配置控制。
- 直写 `create_artifact` / `write_file` 默认关闭。只有显式开启 `enable_direct_file_writes` 且配置固定 `expected_version` 后，才可能进入既有 Muse 审批与单独覆盖审批；上游 0.25.0 的已知写入挂起报告仍须在确切版本完成 live smoke test 后才能解除此限制。
- CLI 新增只读 `skills` / `plugins` 子命令，并支持 `--skills-json` 多 skill 入队；所有创作仍由 Muse TaskQueue 管理，保持人工审批和 manual recovery。
- 已核对既有打通链路：`pyproject.toml` 注册 `muse-open-design = muse.open_design:main`；Worker 将 `open_design_run` 列入强制审批任务，并在消费端使用惰性 `OpenDesignTaskHandler`。本轮没有另建 scheduler、Worker 或独立队列。
- 当前 `config/muse.toml` 原有设置为 `auto_start_daemon=true`、`expected_version="0.27.1"`、`allowed_agents=[]`；本轮未改动这份配置。故按当前配置运行时，daemon 不健康可能触发显式配置的自动启动，但因 agent allowlist 为空，生成仍关闭。未设置 `enable_direct_file_writes`，直写保持关闭。

### 当前边界与未验收项
- 本轮只改 Muse MCP 客户端、OpenDesign 适配器、专项单测及本文档；没有启动、升级或写入 OpenDesign，没有创建项目、运行生成任务或使用 Cloud/BYOK。
- 已新增 MCP 协议级 `resources/list` / `resources/read`，只暴露 `od://skills/<id>/SKILL.md` 与 `od://design-systems/<id>/DESIGN.md`；读取前必须匹配实时资源目录，返回 URI 必须与请求一致，内容有 512 KiB 限制。已加入对应 fake-client 和协议层测试。
- 成功 run 之后现在会调用 `get_artifact(project, include=all, maxBytes=512KiB)` 拉取项目源文件 bundle，核对 `projectId`、路径安全、重复文件名、文本内容完整性，并为文本文件计算 SHA-256；二进制只记录 MIME/大小，不伪造内容哈希。bundle 被截断或有跳过文件时 `complete=false`。这仍是任务结果中的内存证据摘要，尚未持久化到 Creator Initiative 产品账本或 Research Wiki。
- 首次改动后的专项测试曾通过：`tests/test_open_design.py` 22 项 OK，`tests/test_software_crafting.py` 37 项 OK；但这是 Artifact bundle 接入前的快照。Artifact 证据链新增后重跑时 psmux 返回 `another_psmux_command_is_in_flight`，随后 `muse-dev list` 报告一个活动任务仍占用共享执行锁，因此最新代码版本尚无回归通过证据，必须等待共享执行器空闲后重新跑两组测试。
- preview URL 是当前 daemon 会话链接，不是持久交付物；即使 run succeeded，也只有 `artifact_evidence.complete=true` 才代表 OpenDesign 的文本/二进制文件清单未截断且未跳过。它仍不等于浏览器视觉 QA 通过，也不代表可发布视频。
- 本轮没有重新做 live 验证。Session 历史中存在一条 2026-11-16 的成功 live doctor 记录：当时 daemon_status=`started`、PID 32112、health_version=`0.27.1`、22 个工具、0 个项目、0 个 Skills、478 个 Plugins，且 `allowed_agents=[]` / generation_disabled。该记录证明当日曾完成真实工具发现，但属于旧快照，不能证明今天的 daemon、安装版本或 recipe 可用性；本轮没有再次启动 daemon。继续 live 验证前需先核实当前状态并遵守用户对启动/升级的授权边界；若需升级，必须先备份完整用户数据目录并重新核验当时官方稳定版。
- 当前仍缺：将设计系统资源自动注入创作 prompt（需把资源 ID/摘要纳入 Muse 审批指纹并在执行前复核）；把 Artifact Evidence 持久化进 Creator Initiative / Research Wiki；浏览器截图/交互 QA；最新修改后的专项与全量回归。
- 最近一次 `muse-dev list` 同时回报 compileall 曾失败于 `src/muse/routines.py` 第 5 行 import 粘连；随后只读复查该文件第 5–7 行已显示为分离的两条合法 import，疑似并行任务中的瞬时编辑状态，不据此修改该文件。仍需在共享执行锁释放后重跑 compileall，确认稳定状态。

## 2026-11-21｜后续修复与回归结果（以本节覆盖前述旧快照）


- OpenDesign MCP adapter 已实现 `resources/list` / `resources/read`，仅接受实时目录中 `od://skills/` 与 `od://design-systems/` 资源；校验返回 URI、文本类型与 512 KiB 限制。
- OpenDesign CLI 的 enqueue-run 已支持 design-system-uri 与可选 initiative-id；两者均进入人工审批 payload/fingerprint。设计资源在执行前重新计算 SHA-256，Creator Initiative 在入队与执行前都会核验存在。
- `enqueue_run(..., design_system_uri=...)` 已读取 `DESIGN.md`，把 URI 与 SHA-256 放入 TaskQueue 审批 payload/fingerprint；执行前重新读取并比较摘要，资源在审批后变化时拒绝启动 run。资源正文作为不可信设计参考注入 prompt，并受总 prompt 大小限制。
- 成功 run 后通过 get_artifact 收集有界证据，核对项目身份与安全路径，并为文本文件计算 SHA-256。此证据现已写入 Research Wiki 的 bundle/file 节点与关系边，并可选关联 Creator Initiative；详见第 136 节。
- 历史基线：Artifact Evidence 持久化接入前，全量回归 Job wc_job_NnjFducCVVvOo649 曾通过 2,186 项（1 skipped）。持久化后的全量回归状态见第 143 节，不应把旧基线当作当前全量通过证明。


- 浏览器视觉/交互 QA、真实 daemon tools/list 与完整 live 端到端生成仍未验收。当前 allowed_agents 为空；未获明确授权前不得启用生成 agent，也不启动 Cloud/BYOK。
- 本节测试数字与状态取代本文件第 116、119、120 行所记的旧待验收快照；旧记录保留作审计历史，不作为当前状态。


## 2026-11-21｜Research Wiki 持久化与最新回归（当前状态）

- 新增 src/muse/open_design_evidence.py：将 OpenDesign 成功 run 的有界 artifact evidence 规范化为稳定 JSON 快照，并计算快照 SHA-256；通过既有 ResearchKnowledgeStore.upsert_node() 写入 bundle source 节点、逐文件 source 节点及 contains 关系。仅写路径、MIME、大小、文本 SHA-256 与状态元数据，不复制生成源码或二进制内容。
- 快照 external_ref 绑定 project/run/snapshot digest；逐文件 external_ref 绑定 bundle 与规范化路径摘要。重复消费同一 task/run/evidence 时 upsert 幂等，不重复创建节点/边。
- OpenDesignTaskHandler 已接入成功 run 后持久化钩子；默认使用 data/research/knowledge.sqlite3 与 data/initiatives/muse.sqlite3，也允许构造时注入数据库路径。持久化失败会在任务结果中显式返回 artifact_evidence_persistence.status=failed 和有界错误信息，不会伪报成功入库。
- enqueue-run 新增可选 --initiative-id。入队时校验 Creator Initiative 存在，并将 ID 写入人工审批 payload；执行前再次校验，成功后将 bundle node 关联回该 Initiative。--design-system-uri 仍将 URI/SHA-256 纳入审批指纹，执行前复核资源摘要。
- OpenDesign/MCP/Research Wiki 定向回归：OpenDesign adapter、artifact evidence 持久化、软件制作适配与 CLI 审批链均已通过；视频视觉证据、旁白韵律桥接及其 API 集成亦通过。最近一次整仓定向组合回归 86/86 通过。
- 修复全仓角色权限入口：AgentRoleRegistry.authorize() 原先调用不存在的 get()，导致权限校验、SpecialistRouter、Cyber Cosmos API、UnifiedRuntime/Commander 多处失败。现补回 get(role)，返回与 get_scoped() 一致的标准化策略对象；未登记角色和越权能力仍 fail-closed。相关 orchestration、specialist router、Cyber Cosmos API 与 UnifiedRuntime 定向回归 106/106 通过。
- 视频任务恢复验证：video_task_worker 场景检查点失败/重试模块 26/26 通过，video_studio 工作流模块 22/22 通过；视觉证据/联系表校验和 API 端到端定向用例通过。
- 最新稳定全量回归 Job wc_job_PWRMAUXjleRfEdH-：py_compile/compileall 通过；unittest discover 运行 2,226 项，全部通过，1 项 skipped，退出码 0。前几次全量测试曾因角色 get() 缺失、单个视频检查点/视觉用例瞬时失败而未通过；角色入口修复后相关模块与最终全量复核均通过。先前 Windows SQLite 临时文件清理告警未在最终成功回归中造成测试失败。
- 仍未完成 live 验收：真实 OpenDesign daemon/MCP tools/list、已授权 agent 与 recipe 可用性、实际生成、artifact 拉取与 Research Wiki 入库的端到端验收；当前配置 allowed_agents=[]，没有擅自启用生成 agent。浏览器截图/响应式/交互 QA 尚未执行，因为尚无经真实生成确认的预览产物。没有升级或启动 OpenDesign，没有使用 Cloud/BYOK。
- 后续执行边界：先保持当前全量通过基线；待用户配置并授权可用 agent 后，再做本地 daemon 只读健康/工具发现及审批门控 live smoke test。任何真实生成继续遵守静态 allowlist、TaskQueue 人工审批和不自动发布原则。
## 2026-11-21｜最新一致快照回归补记

- 在共享 Runner 没有其他运行中 Job 后，重新执行 py -3.11 -m compileall -q src/muse tests 与完整 unittest discover -s tests -p "test_*.py"；本次实际运行 **2,237 项，全部通过，1 项 skipped，0 失败，退出码 0**。
- 前一轮并行开发期间的全量测试曾观察到视频视觉证据/联系表和镜头检查点用例在不同文件更新时点出现不一致；随后单独复现相关 7 个用例全部通过，并在稳定快照下重跑整仓 2,237 项通过。不要将并行期间的瞬时失败当作当前仍存在的回归缺陷。
- 本次结果更新了第 145 行较早的 2,226 项基线；后续应以当前 2,237 项快照为最新本地回归记录。该回归不等于真实 OpenDesign daemon/MCP live 验收，也不证明任何 agent 已获授权。
## 2026-11-21｜本机运行时只读状态核验

- 仅执行 loopback GET http://127.0.0.1:7456/api/health，没有启动进程、MCP 子进程或 Open Design daemon；当前未发现 Open Design 进程，health 请求连接失败。
- 配置仍为 auto_start_daemon=true、expected_version=0.27.1、allowed_agents=[]。本次没有触发自动启动，也没有变更 agent allowlist、全局 MCP 配置或 Open Design 用户数据。
- 因 daemon 当前未运行且无获准 agent，真实 MCP tools/list、run/artifact 读回、Research Wiki live 入库及浏览器预览 QA 均继续标记为未验收；不得以本地 mock/单测代替。
## 2026-11-22｜MCP stdio 单请求修复与全量回归

- 修复 MCPStdioClient 与 Open Design 0.27.1 packaged daemon-cli.mjs 的协议交互：单工具调用不再走一次性 subprocess.run，而是与多工具批次一样使用单个持久 stdio 会话；resources/list / resources/read 也复用该会话并声明 resources capability。
- Open Design MCP 子进程默认关闭 Vela 远端遥测与 next-task observability（OPEN_DESIGN_VELA_TELEMETRY=off、OD_NEXT_TASK_OBSERVABILITY_MODE=off）；显式传入环境覆盖仍保留。没有修改 agent allowlist，也没有提交生成任务。
- 对未经过完整构造的旧测试/兼容调用增加 120 秒默认超时回退，避免缺少实例属性时产生 AttributeError。
- 新增 tests/test_open_design_mcp_runtime.py，覆盖单工具持久会话、资源目录/正文读取、遥测默认关闭与空白名单。Open Design + software crafting 定向回归 66 项通过。
- 最新稳定全量回归：compileall 成功；unittest discover 实际运行 2,243 项，全部通过，1 项 skipped，0 失败，0 错误，退出码 0。一次未设置 src import path 的误启动造成 97 个导入错误，不作为产品缺陷；修正启动方式后最终全量回归通过。
- 本次只读 live smoke 尝试曾运行到错误的诊断清理调用（Adapter 不提供 close()），因此不将其计为 MCP live 验收。复查后 7456 端口关闭、Open Design 进程不存在；真实 tools/list、list_projects、资源读取、artifact 回读和 Research Wiki live 入库仍需后续单独验收。
- 当前安全边界维持：allowed_agents=[]，不启动生成、不更改 Agent 授权、不写 Open Design 项目数据。
## 2026-11-22｜真实 MCP Doctor 成功与本地 Agent 就绪核验

- 使用已安装的 Open Design 0.27.1 packaged daemon + MCP stdio 执行只读 `OpenDesignAdapter.doctor()`，真实返回 `status=connected`、22 个工具、2 个现有项目、4 个 Agent（`codex`、`opencode`、`byok-opencode`、`antigravity`）、0 Skills、478 Plugins、0 MCP Resources。没有创建项目或执行生成。
- 该次 Doctor 的 `list_projects` 已真实成功，返回两个已有项目；`resources/list` 也真实成功，但目录为空（0 项），因此 `od://design-systems/.../DESIGN.md` 目前没有可用资源 URI。插件目录中存在可用设计系统工作流，包含 `design-system-cosmic`、`design-system-mission-control`、`design-system-pacman`、`design-system-dithered`、`design-system-fantasy`、`design-system-retro`、`design-system-totality-festival`。CLI 已支持 `--plugin`，后续可用插件选择替代空资源目录。
- Muse 适配器环境默认设置 `OPEN_DESIGN_VELA_TELEMETRY=off` 与 `OD_NEXT_TASK_OBSERVABILITY_MODE=off`；本次 daemon 日志确认 `effective run sink={kind:none}`、task observation `mode=off`、`readyToSend=false`。没有向 Vela 发送任务或生成内容。
- Open Design Doctor 返回 `allowed_agents=[]`、`generation_enabled=false`。四个 Agent 中只有 `opencode` 配置指向本地 Provider，但其 OpenCode 配置 URL 为 `http://127.0.0.1:1235/v1`；当前 LM Studio 监听端口为 `1234`，1235 没有监听。`lms ps` 显示没有模型加载；`lms ls` 显示本机有 5 个模型文件（含 DeepSeek Qwen3.5 9B `deep` 与 Gemma 4 E4B），但文件存在不等于模型服务已就绪。故 `opencode` 尚未通过本地推理实测，不得加入 allowlist；未加载模型，避免占用用户 GPU 视频工作资源。
- Doctor 运行期间 daemon 成功启动并完成 MCP 只读请求；诊断脚本退出后 PID/7456 listener 已消失，表现为按需启动后退出，尚未验证长期驻留/崩溃恢复。
- 本次最新本地验证：`py_compile` + Open Design MCP runtime、Software Crafting、Open Design 专项 66/66 通过；整仓 `compileall` 成功，`unittest discover` 2,243 项通过、1 项跳过、0 失败/错误。
- 本次没有执行 `start_run`、没有调用 Cloud/BYOK Agent、没有创建/写入 Open Design 项目或生成 Artifact。浏览器视觉 QA、真实生成与 Artifact → Research Wiki live 入库仍待本地模型 endpoint 对齐并加载模型后验收。
## 2026-11-22｜生命周期、Evidence 完成门禁、LM Studio 对齐与最终回归

- 修复 OpenDesignAdapter 资源生命周期：增加幂等 `close()`、`__enter__/__exit__`；关闭仅释放 adapter 自己持有的 clients，不停止用户 daemon；`__exit__` 在已有业务异常时不让清理异常覆盖原始异常；关闭后拒绝新 daemon 操作。CLI 改为 `with OpenDesignAdapter.from_installed(...)`。新增 `tests/test_open_design_adapter_lifecycle.py`，4/4 通过；真实 CLI `projects` 命令在 context manager 下 exit 0，返回两个现有项目。
- 修复 Worker 假成功：`open_design_run` 只有在 `artifact_evidence_persistence.status == "persisted"` 时才标记 succeeded；缺失或失败时标记 `open_design.evidence_persistence_failed`，并记录 storage 分类与恢复提示。瞬时 SQLite/IO 类错误单独标注 retryable；不创建新的 Open Design run。新增 Worker 成功/失败门禁测试。
- 将 Muse 的 OpenCode 配置切换为项目管理的 `config/opencode.local.jsonc`，原用户级 `%USERPROFILE%\.config\opencode\opencode.jsonc` 保持不变。Muse 模型 endpoint 与 OpenCode Provider 对齐至 LM Studio 实际运行的 `http://127.0.0.1:1234/v1`，模型键为本地 `deep`；LM Studio CLI 确认 server 在 1234，`lms ps` 仍无已加载模型。没有自动加载模型、没有占用 GPU；`allowed_agents=[]` 继续保持。
- 修复本地视频产品代理上游连接释放：二进制流读完最后一块后、写给慢客户端前关闭上游连接；HTTPError 响应也进入 finally 清理。避免浏览器慢读导致本地 API socket 长时间占用。Dashboard、Evidence、Adapter lifecycle 定向测试 21/21 通过。
- 最新一致快照全量回归：`compileall` 成功；`unittest discover` 实际运行 **2,259 项，全部通过，1 项 skipped，0 失败，0 错误，退出码 0**。这次回归覆盖前述 MCP 持久会话、遥测关闭、项目本地 Provider、Worker Evidence 门禁、Adapter 生命周期及 Dashboard 流式清理。
- 真实 Open Design CLI `projects` 命令已通过 context manager 完成只读 smoke；没有执行 `start_run`，没有调用 Cloud/BYOK Agent，没有创建或写入 Open Design 项目。浏览器预览视觉 QA、真实生成、Artifact → Research Wiki live 入库仍待 LM Studio 加载 `deep` 后，先对 `opencode` 做本地推理实测，再由用户明确授权后启用白名单。
- 本节更新并 supersede 第 172–174 行较早的 `1235` 外部 OpenCode 配置与 `2,243` 项回归记录；当前应以项目本地 `1234/deep` 配置与 **2,259 项全绿**为准。原用户级 OpenCode 配置未改动。

## 2026-11-22｜Adapter 生命周期闭环与稳定整仓回归

- src/muse/open_design.py 增加幂等 close()、__enter__/__exit__ 和关闭后拒绝新操作的守卫。关闭时只清理 Adapter 自己持有的 MCP client，不终止或杀死 Open Design 用户 daemon；兼容没有 close() 方法的旧 client，并对同一 client 去重。
- 关闭流程会尝试清理全部 client，正常退出时报告首个清理异常；若任务本身已经抛出异常，则不让清理异常覆盖原始任务异常。
- OpenDesignTaskHandler.execute_task 和 CLI main() 均使用上下文管理器，确保成功、失败和返回路径都执行收尾。
- 新增 tests/test_open_design_lifecycle.py 覆盖重复关闭、client 去重、多个 client 清理、legacy client、上下文退出、异常保留和关闭后拒绝操作；更新 Artifact Evidence Worker mock 以符合 context-manager 接口。
- Open Design / MCP / Software Crafting / Evidence Persistence 定向回归：81 项通过。
- 最终稳定整仓回归使用 Python 3.11（该环境已安装 tzdata 2026.2）：compileall 成功；unittest discover 2,259 项全部通过，1 项 skipped，0 failures，0 errors，退出码 0。
- 额外排查 Python 3.14 全量回归中的 54 个时区错误：Python 3.14 环境未安装 tzdata，而 pyproject.toml 已声明 Windows 条件依赖 tzdata>=2024.1。这属于该解释器环境未安装项目依赖；不通过业务代码硬编码 UTC 偏移规避。后续应在 Python 3.14 的专用项目环境安装项目依赖后再将其纳入正式验证。
- 本次最终只读健康检查发现 127.0.0.1:7456/api/health 当前不可达，配置仍为 auto_start_daemon=true、expected_version=0.27.1、allowed_agents=[]。本轮未自动启动 daemon、未启用 Agent、未执行生成；真实执行/Artifact 拉取/Research Wiki live 入库/浏览器 QA 仍未验收。

## 2026-11-22｜Doctor/CLI 与空项目 Artifact 探针补充（覆盖上条健康状态）

- 在上条健康检查之后，重新使用 Muse 项目本地 OpenCode 配置执行真实 `OpenDesignAdapter.doctor()`，实际启动 packaged daemon 并经 MCP 返回 connected、22 tools、2 projects、4 agents、478 plugins、0 skills、0 resources；`allowed_agents=[]`、`generation_enabled=false`，遥测与 task observation 均为 off。
- 随后真实 CLI `projects` 命令在 Adapter context manager 下 exit 0，列出两个已有项目。CLI 结束后 daemon PID 与 7456 listener 消失，说明当前是按需启动/空闲退出行为，不代表持续驻留；没有 kill 或更改用户 daemon。
- 对名称为“测试”的空项目做只读 Artifact 探针：`get_project` 返回 `entryFile=null`，`list_files` 返回 0 文件；`get_artifact` 因项目没有 entry file 返回明确错误。这是空项目数据状态，不是 MCP 连接失败。没有读取另一个住宅项目的文件内容，也没有创建/修改任何项目。
- 因测试项目为空，真实 Artifact → Research Wiki 写入没有执行；持久化路径目前由 Mock/临时 SQLite 端到端测试覆盖。真实生成、浏览器 QA 与非空项目 Artifact 读回仍待本地 `deep` 模型加载并由用户授权 `opencode` 后进行。
- 本节 supersede 上一节“本轮未自动启动 daemon”的表述：本次确实成功按需启动并完成 Doctor/CLI 只读 smoke；daemon 在客户端退出后停止监听。

## 2026-11-22｜Artifact 完整性闸门与可恢复重试

- OpenDesignTaskHandler 现在即使成功把 Artifact 元数据写入 Research Wiki，也会检查 artifact_evidence.complete。若证据为空、被截断或存在 skipped files，则保留不完整元数据并返回 status=incomplete，不将不完整交付伪装成已完成。
- Worker 对 incomplete 证据设置 open_design.artifact_evidence_incomplete、retryable=false，并要求检查原项目输出；修复源产物后必须创建新的审批任务和新 task ID，不得反复重试同一逻辑 run。
- Research Wiki 数据库临时故障仍单独标记为 open_design.evidence_persistence_failed；已完成的生成可以使用原任务恢复入库。
- Open Design requestId 由稳定 Task ID 确定。新增回归验证：同一 task_id 重放时，start_run 的 requestId 和完整 arguments 保持一致。Open Design 官方 agent-plugin 操作说明要求对同一逻辑运行的重试复用原 requestId；该行为由 Open Design daemon 负责幂等去重。
- 新增 Worker 不完整证据失败关闭测试、Handler 部分 Artifact 持久化测试，以及同一 Task ID 重试参数一致性测试。相关专项回归 **121 项通过**。
- 最终稳定整仓回归：Python 3.11 compileall 成功；unittest discover **2,261 项全部通过，1 项 skipped，0 failures，0 errors，退出码 0**。
- 当前只读 health probe 仍显示 Open Design daemon 未运行，allowed_agents 仍为空；本轮没有启用 Agent、修改全局模型配置或执行生成任务。真实生成和浏览器视觉 QA 仍需在获准 Agent 与 daemon 恢复后验收。


## 2026-11-22｜Muse 本地 OpenCode/LM Studio endpoint 只读核验

- config/opencode.local.jsonc 存在，Provider baseURL 为 http://127.0.0.1:1234/v1，model ID 为 muse-local/deep；未发现 apiKey/token/password 字段。用户级 OpenCode 配置未读取或修改。
- LM Studio OpenAI-compatible API 的 1234/v1/models 返回 HTTP 200，暴露模型 ID：deep、gemma-4-e4b-it-claude-opus-4.5-heretic-uncensored-thinking、eva-abliterated-ties-qwen2.5-14b-i1、voxcpm2、text-embedding-nomic-embed-text-v1.5。1235 端口不可达。
- lms ps 明确显示当前没有加载中的模型；lms ls 显示本地磁盘有 5 个模型文件。模型文件存在及 API 列表可读不代表推理服务已就绪，本轮没有加载模型或占用 GPU。
- 因此 OpenCode 本地配置 endpoint/model 名称已与 LM Studio 当前 API 暴露项对齐，但 inference smoke、Agent allowlist 与真实生成仍未完成。下一步需要先决定是否加载 deep（会占用用户 GPU/内存），再做一次受限本地推理验证；未通过前保持 allowed_agents=[]。
