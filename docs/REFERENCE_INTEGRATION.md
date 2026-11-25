# 外部引擎超融合：Ruflo / dsh-superagent / Super Agent Party

## 架构决策

Muse 是总指挥和权限所有者。成熟外部项目通过窄适配器接入，不在 Muse 内部复制一套同类实现。

| 参考项目 | 许可证与状态 | 采用方式 |
|---|---|---|
| https://github.com/ruvnet/ruflo | MIT；本地锁定快照 | 以官方 CLI/MCP 作为蜂群、Agent、记忆、学习和联邦执行引擎。Muse 保留目标、权限、任务账本、审批、审计和最终验收。 |
| https://github.com/deepseek-ai/dsh-superagent-prototype | MIT；研究原型，依赖 DeepSeek Harness | 复用 Trigger → Eval → Ticket → Assignment 生命周期、授权策略、独立工作目录、持久化交接、重试恢复和注意力优先控制台设计。对照 Muse 现有队列做增量适配，不把 Harness 运行时说成已嵌入。 |
| https://github.com/heshengtao/super-agent-party | 默认 AGPLv3；闭源商业用途需商业许可 | 作为可选外部桌面伴侣，通过 OpenAI 兼容 API / MCP 对接；未经明确许可决策，不复制或静态链接其 AGPL 核心代码。 |

## 本机参考快照（2026-11-03）

- Ruflo：commit 5f3dacc54b7279e39b172d6027d7989791448e2e
  - 本地源码：<AI_AGENT_ROOT>\\reference-projects\\ruflo
  - npm：ruflo@3.71.0；CLI 与依赖已从 npm 缓存移动到 D:\\Tools\\Ruflo\\npx-cache（没有做全局 npm 安装）。
  - 本机 MCP 工具清单实际返回 385 项。
  - 实际调用 system_status 成功，返回版本 3.71.0、status=healthy；memory_search 也成功返回，当前 patterns 命名空间为空。
- dsh-superagent-prototype：commit 6337fc823ce20251c8de8085948165c81aaa701e
  - 本地源码：<AI_AGENT_ROOT>\\reference-projects\\dsh-superagent-prototype
  - README 明确说明这是研究代码，并非生产就绪产品。
- Super Agent Party：浅克隆因仓库内大型媒体/模型资产而 TLS early EOF；已删除不完整副本，没有复制其源代码。
## 能力边界

- Muse：用户意图、项目选择、权限策略、任务队列、人工审批、事件历史、数据分级、回滚和最终验收。
- Ruflo：Swarm 拓扑、Agent 池、工具目录、记忆/神经学习和可选跨设备联邦。通过官方 CLI/MCP 调用。
- dsh 设计：消息接入/授权/Ticket 生命周期、持久化交接、失败恢复，以及只突出需要人处理事项的面板。
- Super Agent Party：可选聊天/桌面伴侣、扩展生态、OpenAI 兼容 API 和 MCP 接口。

## Ruflo 适配器

新增 muse.ruflo_adapter.RufloMCPAdapter，通过官方 ruflo mcp exec 命令调用 MCP 工具，使用 argv 和 shell=False；from_installed() 自动解析 D 盘固定版本 CLI，也支持 MUSE_RUFLO_NODE / MUSE_RUFLO_CLI 覆盖。

- 默认只读工具白名单：system_status、system_health、swarm_status、agent_list、memory_search 等。
- 其他非只读工具必须同时显式加入 enabled_tools，并在每次调用时传入 approved=True。
- system_reset、terminal_execute、terminal_create、terminal_close、browser_act、mcp_stop 永久禁止经此桥接器调用。
- 参数必须是有限 JSON 对象，最大 32 KiB；输出最大 128 KiB。
- 不复制 Ruflo 内部实现，不维护第二份 Ruflo 工具注册表。
- 本地模型执行：显式设置 `MUSE_RUFLO_PROVIDER=lmstudio`；可选 `MUSE_LMSTUDIO_URL`（默认 `http://127.0.0.1:1234/v1`）和 `MUSE_LMSTUDIO_MODEL`。Bridge 验证 loopback 并从 `/v1/models` 解析模型，再映射到 Ruflo Ollama-compatible Provider；不需要虚构 API Key，不保留云端密钥，也关闭本地失败后的 Provider fallback。
- 新增命令：muse-ruflo status / tools / call；变更类工具还必须传 --enable-tool 和 --approve。
- 真实 smoke test：D 盘 Ruflo 3.71.0 system_status 健康；memory_search 返回 0 条结果（空库，不代表工具失败）。

## agent-desktop 桌面操作引擎

- 上游：`https://github.com/vercel-labs/agent-desktop`，Apache-2.0；本机 npm 版本 1.0.2，安装于 `D:\\Tools\\AgentDesktop`。
- Windows x64 原生可执行文件路径固定在 npm 包的 `bin/agent-desktop-win32-x64.exe`；通过 argv + `shell=False` 调用，读取 JSON envelope。
- 新增 `muse.desktop_adapter.AgentDesktopAdapter` 和 `muse-desktop` CLI；只暴露明确命令白名单。观察类命令默认只读；语义动作逐次要求 `approved=True`；高风险输入/关闭/任意鼠标键盘注入命令 fail-closed。
- 实测 `status` 返回 accessibility=granted，`list-apps` 成功；尚未做实际 click/type UI 验收。
- 当前 CLI 的 state_root 仍指向用户 C 盘目录。安装路径 D 盘不等于运行状态迁移完成；待确认官方配置方法后再迁移。
- 统一运行时支持 `desktop-snapshot --task-id <ticket>`：成功观察后只写入 `desktop.observation_captured` 元数据事件，不把截图、UI 文本或可访问性树内容写入任务账本；未知 Ticket 在调用桌面前即拒绝。
## 超融合实施状态（本轮）

- **Commander/Ruflo：** Muse 保留唯一审批与 TaskQueue 账本，Ruflo 提供 Swarm/Agent 生命周期。已接通显式 LM Studio 本地 Provider：Ruflo 通过 `OLLAMA_BASE_URL` 调用本机 OpenAI-compatible endpoint，无需 Anthropic Key。真实 `agent_execute` 尚未进行 live smoke test；需对一个明确 Ticket 单独批准后才能验收实际 Agent 执行。
- **DSH Ticket：** 已复用 Muse TaskQueue SQLite，增加追加式 `task_events`，任务创建、领取、状态变化、报告提交、取消、人工批准均自动记账；Mandate/外部 Agent 事件可通过统一事件 API 写入。
- **Party：** 仅作为独立可选服务；目前没有运行实例或已验证连接。接入时优先 loopback MCP `/mcp` 或 OpenAI-compatible `/v1`，并继续遵守 AGPLv3/商业授权要求。
- **Memory/Graph：** Muse 自己的 `MemoryStore` 仍是 canonical memory；统一运行时新增只读联邦检索，同时查 Muse 本地记忆和 Ruflo `memory_search`，可用 `--local-only` 禁用远端查询。不会自动把个人记忆写入 Ruflo；双向同步、来源血缘、删除/纠错传播仍未实现。
- **Desktop：** agent-desktop 已进入统一运行时，可执行健康检查和快照；写操作仍逐次审批，尚未做实际 UI 修改/回读验收。
## 后续集成阶段

1. 把 Ruflo 的 Swarm/Agent/mission 工具逐项映射到 Muse AgentRegistry 和 Commander，并先只读、后逐项审批开放。
2. 在验证 Ruflo mission/routing 工具契约后，用其替换临时 Commander 规划器的重复能力。
3. 仅在 Muse TaskQueue 有明确缺口时吸收 dsh Ticket 生命周期，不再新建第二套队列。
4. 只有当 Super Agent Party 服务明确运行且许可证边界确认后，才增加 OpenAI 兼容 API + MCP 连接配置。
## 上游资料

- https://github.com/ruvnet/ruflo/blob/main/README.zh-CN.md
- https://github.com/heshengtao/super-agent-party
- https://github.com/deepseek-ai/dsh-superagent-prototype
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/000-philosophy.md
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/001-domain.md
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/002-authorization.md
- https://github.com/deepseek-ai/dsh-superagent-prototype/blob/main/docs/003-execution.md
