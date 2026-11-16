# Ploid 专项人物研究适配器

更新：2026-11-13

## 定位

Ploid 是 people-intelligence / professional people research 服务，不是 Muse 的通用学术、科学、技术或代码库深度研究引擎。Muse 的 ResearchPipeline、ResearchEvidenceStore 与 ResearchKnowledgeStore 仍是通用研究主干；Ploid 只作为可选的「人物 / 专业背景 / B2B 决策者」专项来源。

官方资料：
- MCP：<https://ploid.com/documentation/mcp>
- 总览：<https://ploid.com/documentation>
- 隐私政策：<https://ploid.com/privacy>

## 本轮已实现

新增：
- `src/muse/ploid_adapter.py`
- `tests/test_ploid_adapter.py`

适配器支持注入式 MCP transport，也新增了本地 stdio transport：
- `PloidStdioTransport` 复用 Muse 现有 `MCPStdioClient`，沿用固定命令、工作目录、工具 allowlist、超时和逐次付费工具审批。
- `PloidMCPAdapter.from_stdio(command=..., workspace=..., run_database=...)` 可直接建立本地 MCP 适配器；每次工具调用都会重新读取当前 `tools/list` 与输入 Schema。
- `search_people`：付费候选搜索；只返回候选，不自动选中身份。
- `research_person`：只接受用户选定的 person_id，或用户明确给出的 LinkedIn profile URL。
- `find_people` / `find_decision_makers`：带人数上限与 ACU 预算门槛。
- `get_research_status`：保存 run_id、状态与最近轮询时间；持久化限制同一 run 至少间隔 5 秒。
- `cancel_research`：必须显式传入用户要求停止的标志。
- `ingest_person_result`：把完成且身份已解析的结果写入现有 Research Wiki：人物研究简报以 proposed idea 保存；来源作为 source 节点；关系只用 relates，不自动生成 supports/refutes 结论；保留来源 URL、字段 provenance、限制说明与 Cycle ID。
成本门槛：
- 人名候选搜索：预算至少 1 ACU。
- 单人深度研究：预算至少 25 ACU。
- People Set：按请求人数上限以每 10 人 1 ACU 向上取整估算。
- 每个付费方法都要求调用方显式提供 `budget_authorized=True` 和足够的 `max_acu`；不满足时不会调用 MCP。

数据边界：
- 只保存专业公开字段白名单（姓名、职位、公司、地点、公开 LinkedIn URL），不保存邮箱、电话或联系方式 enrichment。
- Ploid 的 source-reviewed 不等于 Muse 已独立核验；Research Wiki 标记 `third_party_research_unverified_by_muse`。
- 来源 URL 必须是 HTTPS；来源边只表示 provenance reference，不作为事实裁决。
- 不采集或推断敏感个人资料，不用于跟踪、骚扰、私人用途或不合法的招聘/营销用途。

## 当前状态：用户已完成授权，连接待本机验证

用户在 2026-11-13 明确反馈已完成 Ploid device authorization。当前 WebCodex Runner 可见 Node.js v24.20.0、npm 11.19.0 与 npx，但没有发现 `@ploid/mcp` 的 npx 安装缓存或常见 Ploid 配置目录；npm registry 元数据查询超时。因此不能证明 Runner 与用户授权环境共享同一凭证存储，当前连接状态为 `user_confirmed_authorized / runner_connection_unverified`，不是已连接。

新增只读验证入口：
- `scripts/ploid-verify.ps1`
- `scripts/ploid_verify.py`

在完成授权的同一台 Windows 电脑、Muse 项目根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\\scripts\\ploid-verify.ps1
```

验证脚本通过本地 stdio MCP 进程执行 initialize + tools/list，只输出工具名称和输入 Schema 字段；不执行 tools/call，不发起研究，不读取或打印 token/API key。若输出 `connected`，表示已验证本地 MCP 连接与工具目录；若失败，只报告错误类型，不泄露 stderr 或凭证。

下一步：
1. 在用户已授权的同一环境运行上述只读验证脚本，取得真实 tools/list。
2. 按真实工具 Schema 校准 adapter；Ploid 官方文档存在工具清单与人物搜索工作流的表述差异，不能硬编码假设。
3. 将 Ploid 作为 Commander 的可选人物研究能力注册；未确认本次预算时保持付费工具关闭。
4. 只有用户指定人物/研究目标并确认本次 ACU 预算后，才调用付费研究；结果写入 Research Wiki 时保留来源、provenance、限制和成本。

Hosted MCP endpoint `https://api.ploid.com/mcp` 使用 Streamable HTTP + OAuth 2.1；本轮 Muse 仍采用本地 stdio adapter，没有实现 hosted HTTP transport。Ploid 的人物研究首次成功约 25 ACU、search_people 1 ACU、People Sets 每 10 人 1 ACU；具体以官方工具返回的 usage/pricing 为准，Muse 不推测 workspace 余额。
