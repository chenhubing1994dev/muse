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

## 下一步

1. 将 model_family 从运行时描述符提升为受版本控制的 Agent 配置字段，做向后兼容迁移；未知模型保持 generic，不猜测供应商。
2. 为各 Provider 建立版本化能力档案：上下文窗口、工具调用、结构化输出、多模态、延迟、配额和数据政策；未知值必须保持 unknown。
3. 构建公开/合成任务集，对同一任务做模型盲测；记录准确性、格式遵循、事实支持、拒答质量、延迟、token 与真实成本。
4. 仅把经重复验证的提示策略写入 Prompt Memory；失败案例和来源保留，提案者不得自批。
5. 对外部模型调用继续执行 free-only、额度新鲜度、数据分类与用户授权检查。

## 验证

- Python 3.11 专项单测：提示词引擎 + SpecialistRouter 共 24/24 passed。
- pyproject.toml TOML 解析及 muse-prompt console entry point：通过。
- 实际运行 python -m muse.prompt_engine adapt：JSON 输出可解析，Claude 别名映射为 anthropic，安全边界检查通过。
- 显式 Claude 模型家族在 SpecialistRouter dispatch 中适配提示通过；未声明 model_family 的旧路径由路由回归集覆盖；prompt_engine.py、specialist_router.py 与相关测试 compileall 通过。
- 验证通过项目现有 muse-dev → psmux detached 执行链；Job dbe88e18ccd24f718c9be90666140d4e，exit code 0。
- 未执行云模型调用或上游仓库代码；真实模型效果尚未测量。
