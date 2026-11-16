# Muse 外部平台与多 Agent 设计研究

更新时间：2026-11-02

## 结论摘要

Muse 应保留自有、轻量、可审计的编排核心；把 WorkBuddy、Coze、Gemini API 等视为可选外部能力，不将某个平台当成唯一大脑，也不把会员订阅误认为 API 免费额度。

## 平台观察

### Tencent WorkBuddy

官方资料将 WorkBuddy 定位为具备多步骤任务执行、MCP Server 与 Skills 扩展能力的桌面 Agent 工作台。MCP 可连接外部工具/数据源；文档给出用户级和项目级配置方式。适合先作为“外部执行工作台 / MCP 工具宿主”研究，而不是假设它提供可直接调用的通用模型 API。

- 产品能力说明：https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Product-Guide
- MCP 指南：https://www.workbuddy.ai/docs/zh/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/MCP-Guide
- 官方站点：https://www.workbuddy.ai/

### Coze

官方 API 文档说明可将 Agent 发布为 API；当前文档列出每个注册用户 30 次免费 API 调用额度，超出后会消耗 Coze tokens。免费额度不是无限调用，也不应自动把超额请求转为付费调用。

- API overview：https://www.coze.com/open/docs/developer_guides/coze_api_overview

### Gemini

必须区分 Gemini Apps 订阅权益与 Gemini Developer API 的计费/配额。Gemini API 有独立 Free Tier，但只开放部分模型和额度；实际 RPM/TPM/RPD 依模型、项目和账户而变，应以 AI Studio 显示的当前限额为准。API 文档也明确 Free Tier 的数据使用条款与 Paid Tier 不同。用户的 Gemini AI Pro 订阅本身不能证明 API 已开通免费额度，也不代表 API 不会收费。

- API pricing：https://ai.google.dev/gemini-api/docs/pricing
- Billing / quota：https://ai.google.dev/gemini-api/docs/billing
- Rate limits：https://ai.google.dev/gemini-api/docs/rate-limits
- Gemini Apps plan limits：https://support.google.com/gemini/answer/16275805

### OpenAI Agents SDK（架构参考）

官方 SDK 的设计概念包括 Agent、Handoff、Guardrail、Session 和 Trace；适合参考专家分工、工具交接、人工审批及可观测性。Muse 当前先采用标准库实现 provider-neutral 核心，避免在验证需求前引入额外依赖。

- Agents SDK：https://developers.openai.com/api/docs/guides/agents/sdk
- Orchestration overview：https://developers.openai.com/tracks/building-agents
- Tracing：https://developers.openai.com/api/docs/guides/agents/integrations-observability
- Human review：https://developers.openai.com/api/docs/guides/agents/guardrails-approvals

## 对 Muse 的设计决定

1. 编排内核与模型/Agent 平台解耦；每个适配器声明能力、费用模式、速率限制、数据边界与超时。
2. free_only 是硬性策略：配额不明、无法读取剩余额度、免费额度耗尽、账户需要付费升级时，一律停止该适配器，不自动 fallback 到付费模型。
3. 先接 MCP/本地工作台这类有明确工具边界的入口；模型 API adapter 独立实现，避免将桌面 Agent 产品误当成模型 API。
4. 默认只发送用户明确授权的任务摘要，不上传本地记忆库、个人文件或项目资料；真实云端 smoke test 需单独批准。
5. DeepSeek 相关 Agent 产品名称尚未核实，不以语音转写猜测的名称配置 provider。

