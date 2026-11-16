# Muse B-122 补充进度：Ploid 专项人物研究适配器

日期：2026-11-13

## 已完成

- 新增 `src/muse/ploid_adapter.py` 与 `tests/test_ploid_adapter.py`：动态刷新 Ploid MCP tools/list 与 inputSchema；身份候选不自动选择；单人研究 25 ACU 预算门槛；People Set 预算估算；run_id 持久化；状态轮询至少间隔 5 秒；取消必须有明确用户停止请求；Research Wiki source/idea/provenance 导入。
- 新增 `PloidStdioTransport` 与 `PloidMCPAdapter.from_stdio(...)`，复用 Muse 现有 allowlisted `MCPStdioClient`。付费操作经过 adapter 预算门槛后，才向 MCP client 传递逐次审批。
- 新增 `scripts/ploid-login.ps1`，用于官方 device authorization；用户已在本轮明确反馈「授权好了」。
- 新增 `scripts/ploid-verify.ps1` 与 `scripts/ploid_verify.py`：只做 MCP initialize + tools/list，打印工具名和输入字段，不调用任何 tools/call，不读取或打印凭证值。
- Ploid 适配器定向测试此前通过 **8/8**；视频账本 + Dashboard integration + video product API 组合回归此前通过 **21/21**。
- Python 全量 unittest 此前通过 **1,114/1,114**，compileall 通过；Dashboard Svelte-check 0 errors/warnings，Node API tests 19/19，Vite build 通过。
- 修复异步视频 API 的 HTTP 202 行拼接故障，避免 Worker 启动后客户端 RemoteDisconnected 与测试 teardown 时 MP4 文件占用。

## 授权与连接状态（本轮复核）

- **用户确认授权已完成**；但当前 WebCodex Runner 侧尚不能独立证明它与用户执行登录脚本的环境共享同一凭证存储。
- Runner 可见 Node.js v24.20.0、npm 11.19.0、npx；没有发现 `@ploid/mcp` 的 npx 安装缓存或常见 Ploid 配置目录。npm registry 元数据查询超时。
- 因此当前状态标记为：`user_confirmed_authorized / runner_connection_unverified`，不能误报为 MCP 已连接。
- 新增只读验证脚本。需要在完成授权的同一台 Windows 电脑、Muse 项目目录运行 `powershell -ExecutionPolicy Bypass -File .\scripts\ploid-verify.ps1`。预期只返回 tools/list Schema，不会消耗 ACU。
- 官方文档列出人物研究、People Sets、状态查询与取消工具；人物研究首次成功一般为 25 ACU，候选搜索 1 ACU，People Sets 按每 10 人 1 ACU 计价。任何付费研究都必须在本次任务明确确认预算后才能发起。
- 当前尚未调用 Ploid 研究工具、未消耗 ACU、未把 Ploid 结果写入 Research Wiki，也未注册为 Commander 的运行 provider。
- Ploid 只作为专业人物/决策者研究补充，不替代 Muse 通用 ResearchPipeline。真实 Wan GPU 生成也未在本轮验收。

## 下一步

1. 在用户完成授权的同一环境运行 `scripts/ploid-verify.ps1`，取得真实 tools/list。
2. 按实际 Schema 校准 adapter，确认身份搜索/选择流程。
3. 把 Ploid 作为 Commander 的可选人物研究能力注册；默认关闭付费操作，预算/本次授权通过才执行。
4. 用户指定一个明确人物与研究目的后，展示 25 ACU 预算，再单独取得本次研究确认；完成后将来源、provenance、限制和成本写入 Research Wiki。
