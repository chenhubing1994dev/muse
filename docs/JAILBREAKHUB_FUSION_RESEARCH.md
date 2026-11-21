# JailbreakHub 融合研究：安全的多模型提示词适配

- 上游：<https://github.com/lalipa2003-arch/JailbreakHub>
- 审查日期：2026-11-21
- 上游许可：仓库 LICENSE.md 声明 Unlicense / public domain dedication。
- 范围：静态审查仓库结构与 README、LICENSE；未 clone、安装或执行上游文件。
- 观察：仓库按 ChatGPT、Claude、Gemini、Grok、KIMI、GLM、Qwen、DeepSeek、Multi-AI 等目录组织内容；README 将其定位为 jailbreak、prompt-injection、social-engineering 与安全过滤器探测材料。
- 风险：模型专属提示词未经独立复现，不可视为有效能力说明；提示注入与越权话术属于不可信双用途材料。上游说明其提示词针对官方产品，跨第三方产品不一定适用。
- 采纳：按模型家族组织提示策略；明确任务目标、背景、约束、输出契约、验收标准；根据任务类型和模型上下文特性调整结构；对检索网页、文件、用户粘贴内容做显式不可信数据分隔；建立确定性 prompt lint，再用受控基准做真实模型评测。
- 不采纳：绕过安全限制、角色劫持、系统提示词提取、隐藏指令泄漏、规避供应商风控/降级、诱导有害输出等生产能力。红队样本只允许在隔离、授权的安全评估环境中作为检测样本，不进入默认提示模板。
- 边界：本轮实现的是离线提示构建和规则 lint，不代表已连接或实测任何云模型；不调用 API、不上传上下文、不安装依赖、不修改 Provider 计费策略。

## 本轮实现

src/muse/prompt_engine.py 提供 provider-neutral 的任务规格、模型家族格式策略、可信/不可信上下文分隔、结构化消息构建、质量 lint 和 muse-prompt adapt CLI。SpecialistRouter 的 AgentAdapterDescriptor 可显式声明 model_family；已在普通 dispatch、Commander DAG dispatch 与 pipeline 中按需包装任务提示。未声明模型家族时保持旧行为。模型配置均标注为启发式默认值，不声称经过供应商基准证实。

## 第二阶段：Agent 模型家族持久化

- `AgentRegistry` 的 SQLite `agents` 表新增可空 `model_family` 字段；启动时对旧数据库执行向后兼容的 `ALTER TABLE`，历史 Agent 保持 `NULL`（unknown），不猜厂商。
- `register(..., model_family=...)` 保存显式声明；后续旧调用未提供该参数时通过 `COALESCE` 保留既有值。新增 `set_model_family()` 作为显式设置/清除入口。
- 本地 loopback discovery 配置允许每个 Agent 声明可选 `model_family`；字段由本地配置提供，不采信 health endpoint 自报的厂商信息。旧配置仍可用。
- `SpecialistRouter` 注册时从持久化 Agent 元数据恢复模型家族，再构建运行时 descriptor；只持久化模型标识，不持久化 Python callable，也不自动恢复执行权限。
- 仍然要求显式声明；未配置或历史空值走 generic/旧提示路径。模型家族值只是路由提示策略的选择标签，不代表模型真实能力已被验证。

## 第三阶段：模型能力档案与离线提示一致性基准

- 新增 `src/muse/model_capabilities.py` 的 `ModelCapabilityStore`，独立 SQLite 保存模型档案与逐字段能力记录；支持上下文窗口、工具调用、结构化输出、视觉/音频/视频、延迟、请求/Token 配额、输入/输出价格和数据政策。
- 每个字段独立保存 `value`、`status`（`unknown` / `declared` / `verified`）、`source`、`observed_at`、`evidence_ref` 和 `updated_at`。未记录字段返回 `value=null` / `status=unknown`；不从厂商或型号名称推断能力。`verified` 必须带证据引用，价格零值有效，不根据 token 或运行时长推算费用。
- `run_offline_prompt_benchmark()` 运行固定版本 `muse-prompt-conformance-v1` 的五类合成任务：研究证据、结构化代码审查、多模态任务说明、隐私边界、引用指令越权识别。保存每次运行的用例检查、分数、模型家族、时间和结果。
- 该基准只验证 Muse 的提示构建与确定性 lint，不发起推理、不请求任何 Provider；结果明确标记 `no_model_invoked=true` 和 `not_a_model_quality_measurement=true`。因此不能用于宣称哪个真实模型更聪明或更可靠。真实模型盲测、Prompt Strategy Memory 的晋级门槛和自动路由仍待后续阶段。

## 下一步

1. **已完成**：将 model_family 持久化到 AgentRegistry，并接入本地 discovery 配置与 SpecialistRouter 重载；旧数据库/旧配置保持兼容。
2. **已完成基础档案**：建立逐字段来源/时间/证据/状态的 SQLite 能力目录；当前只记录显式声明或有证据的值，其他字段保持 unknown。
3. **已完成离线提示一致性基准**：固定合成任务可重复运行并持久化结果；这不是模型能力测量。下一步才是在用户授权和费用/隐私闸门下做真实模型盲测，记录准确性、格式遵循、事实支持、拒答质量、延迟、Token 与实际费用。
4. 暂不把静态 lint 通过等同提示策略有效；待真实模型盲测具备独立重复样本后，再将策略写入 Prompt Memory，保留失败案例和来源，提案者不得自批。
5. 对外部模型调用继续执行 free-only、额度新鲜度、数据分类与用户授权检查。

## 验证

- Python 3.11 专项单测：提示词引擎 + SpecialistRouter 共 24/24 passed。
- 第二阶段定向回归：AgentRegistry + AgentDiscovery + SpecialistRouter 共 **41/41 passed**，覆盖旧 SQLite schema 迁移、model_family 持久化/重启、未提供字段时保留旧值、非法值拒绝、loopback 配置接线与 Router 重注册恢复；最终复验 Job `9580e879ea68482f972e555e033e95ba`，exit 0，包含相关模块 compileall。
- 较早全项目回归曾为 1,998 tests / 1 import error / 1 skipped；错误为 `tests/test_jyutping.py` 找不到 `muse.jyutping`。该历史记录与本轮新基准模块无关。
- pyproject.toml TOML 解析及 muse-prompt console entry point：通过。
- 实际运行 python -m muse.prompt_engine adapt：JSON 输出可解析，Claude 别名映射为 anthropic，安全边界检查通过。
- 显式 Claude 模型家族在 SpecialistRouter dispatch 中适配提示通过；未声明 model_family 的旧路径由路由回归集覆盖；prompt_engine.py、specialist_router.py 与相关测试 compileall 通过。
- 验证通过项目现有 muse-dev → psmux detached 执行链；Job dbe88e18ccd24f718c9be90666140d4e，exit code 0。
- 未执行云模型调用或上游仓库代码；真实模型效果尚未测量。
- 第三阶段新增模块专项测试 **10/10 passed**（Job `b15a3b08b6474bb4af2778167baa2b46`，exit 0），覆盖 unknown 默认值、来源/时间/证据保存、字段类型与证据校验、重启持久化、零价格、禁止伪造模型调用的基准记录，以及五用例离线基准可重复运行。
- 随后全项目回归运行 **2,045 tests：1 failure、7 errors、1 skipped**。失败/错误集中在并行改动的 InitiativeStore 视频阶段 checkpoint 参数/迁移、Status API visual review 请求及 Video Human Review digest 断言；本轮 `test_model_capabilities` 未出现失败。由于工作区并行变化，不能把该结果称为全仓通过，也未擅自修改其他 Session 的文件。
