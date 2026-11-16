# Muse Agent Commander：专家目录与智能路由

## 目标
让 Muse 根据任务内容选择具备相关专长的 Agent，并把可解释的分配结果交给既有并行/DAG 编排器。调度器负责分工、顺序、依赖、监控和复盘；专家负责其专业范围内的执行。

## 第一阶段已实现
- `SpecialistRegistry`：SQLite 持久化 specialist_id、role、domains、keywords、capabilities、description、priority、enabled。
- `SpecialistRouter.route()`：对启用的专家进行确定性匹配，domain 命中 4 分、keyword 命中 2 分、priority 用作可审计的轻量排序项；输出每个专家的分数和命中理由。
- `SpecialistRouter.dispatch()`：选出前 N 名专家，构造 `AgentSpec` 并交给 `MultiAgentOrchestrator` 并行执行。
- 只使用已注入本地 callable 的专家；数据库不会保存可执行代码。重启后必须由受信任的 adapter 注册调用函数。
- `AgentRoleRegistry` 在创建 run 前校验专家声明的能力 allowlist。
- 没有匹配项或没有已注入 adapter 时明确报错，不随机选择、不假装成功。

## 专家角色建议
| 专家 | 领域 | 典型任务 |
|---|---|---|
| Researcher | research、web、资料、调研 | 搜集来源、事实核验、竞品研究 |
| Architect | architecture、design、架构、系统设计 | 模块边界、接口、技术选型 |
| Developer | coding、development、代码、编程 | 实现功能、修复缺陷、测试 |
| Reviewer | review、security、审查、安全 | 独立审查差异、权限、回归风险 |
| Data Analyst | data、analytics、数据、分析 | 数据清洗、统计、可视化 |
| Operator | operations、automation、运维、自动化 | 运行检查、任务执行、健康状态 |
内置模板已实现并可幂等安装：Researcher、Architect、Developer、Reviewer、Data Analyst、Operator、Writer、Planner。模板仅登记元数据，不代表这些真实供应商 Agent 已连接或可执行。真实执行能力必须来自显式注册的本地 provider adapter。
## 调度规则
1. 解析用户目标，保留原始请求和约束。
2. 依据专家领域与关键词生成可解释候选排名。
3. 选择最匹配的专家；可并行任务并发执行，有依赖任务走 DAG。
4. 在派发前执行角色能力 allowlist 检查。
5. 记录命中理由、run_id、专家结果、失败和阻塞状态。
6. 由独立 Reviewer 检查高影响产物；不能由产出者批准自己的结果。`IndependentReviewGate` 已实现独立身份检查、结构化 JSON 结果校验、critical/high finding 否决及 SQLite 审计；不自动应用改动或合并。
7. 将人工确认后的成功案例沉淀为 Agent Track 学习候选。

## Provider 与调度现状
- `ProviderRegistry` 已存在：统一 ProviderAdapter 协议、能力筛选、private/sanitized/public 数据分级、LM Studio 本地适配器，以及云端免费额度新鲜度/余额校验。
- 尚未连接真实云端 Provider；Ruflo Bridge 已支持显式 LM Studio loopback Provider，但实际 Ruflo Agent live dispatch 尚未验收。没有实测云端并发容量、速率限制、延迟或成本数据，因此不能声称已完成动态负载/成本调度。
- SpecialistRouter 当前使用确定性词汇匹配；不会把未连接的内置专家伪装成在线 Agent。

## 已知限制与下一步
- 当前是词汇/短语匹配，不是语义理解；中文匹配使用轻量短语匹配，不等同专业 NLP。
- 尚未考虑 provider 实际负载、速率限制、成本、延迟、配额与健康度。
- 尚未自动推导 DAG 依赖，也没有执行时 Tool Gateway 隔离。
- 下一阶段：Provider health/capacity telemetry、负载感知调度、语义分类器评测集、CLI 专家管理、Reviewer 接入任务完成流水线，以及桌面 UI 的实时路由可视化。