# Shendeng 上游评估与 Muse 独立融合方案

更新：2026-11-19

## 1. 上游身份与范围

- 仓库：https://github.com/txga01Lwz/shendeng
- 名称：神灯 · 东方心理学
- 默认分支：main；公开仓库；仓库元数据 ID：1461235210。
- 仓库主体是方法论文档与 AI Skill：README、SKILL、22 条方法条目参考文档、许可证及英文版；不是可直接嵌入 Muse 的软件库或 Agent 框架。
- 已读取上游 README.md、SKILL.md、references/gongshiku.md、LICENSE。对应 Git blob SHA：
  - README.md：ca60af5ebcfbf130fa5d1453f48806d66c7b1cf6
  - SKILL.md：24cfa8bd428676be212ef82b8a1dc1f26474de96
  - references/gongshiku.md：6eda12b0d9db4e3a31fae2839b15bf3fb50263a3
  - LICENSE：ab127ed5793e2b55fcfab8b883a61478d181a2a6

## 2. 许可与商业化硬边界

上游 LICENSE 明确标注 CC BY-NC 4.0，要求保留指定作者/作品署名及来源，并禁止未经授权用于商业销售、收费产品集成或商业运营；商业用途须向作者取得授权。

Muse 是长期生产力产品，存在商业化可能。因此本项目采取保守边界：

1. 未取得作者明确商业授权前，不复制、改写、翻译、打包、分发上游 SKILL、22 条条目、具体公式文本、独特表达或其衍生内容。
2. 不把上游 Skill 安装到 Muse、作为内置提示词、模型知识包或商业服务内容。
3. 本评估只记录仓库事实、许可限制及高层能力观察；后续实现必须由 Muse 团队独立设计和撰写，不以逐条改写上游条目作为开发方式。
4. 若未来确需直接使用上游材料，先取得书面商业授权并归档授权范围、期限、署名方式及再分发条件。
5. 本文是工程风险记录，不构成法律意见。

## 3. 上游能力观察（非效果背书）

上游材料覆盖三类应用情境：个人自我理解、人际/团队互动、产品与客户理解。其主要设计特点包括：

- 从具体情境收集信息，而不是仅根据一个行为标签直接下结论。
- 同时考虑身体/情绪/念头/需求/互动环境等不同信息层次。
- 先区分问题类型，再选择适当的帮助路径；对超出适用范围的情况提出转介。
- 将解释视为待检验假设，要求考虑其他可能原因。
- 把建议落实为小规模行动，并设置可观察的检查信号。
- 复杂情境不只观察单一当事人，也考虑其他参与者及环境因素。
- 强调记录过程、回看行动结果并调整下一步。

这些是对文档结构的工程性概括，不表示相关心理学主张已获得独立临床验证。仓库中有关服务规模、效果及专业认可的陈述属于作者/项目方自述；本轮未找到独立第三方核验，Muse 不应把它们写成已证实事实。

## 4. 与 Muse 现有能力的重叠

只读检查确认，Muse 已有多项相邻基础设施，不应重复建设：

| 能力 | Muse 现有基础 | 结论 |
|---|---|---|
| 事实、假设、反证与替代解释 | ResearchKnowledgeStore：claim、experiment、idea、failure 节点；supports/refutes/alternative 等关系；证据质量、来源独立性与追加式审查记录 | 复用现有 Research Wiki |
| 本地记忆与用户/Agent 隔离 | MemoryStore typed memory、User/Agent 双轨、候选审批；MemoryReflection 只生成待审关联 | 不新建第二套记忆库 |
| 研究和反向检索 | AutonomousResearchEngine、support/counter query、拒绝审查后的研究 handoff | 复用现有研究引擎 |
| 任务计划与验收 | TaskQueue、Commander、WorkflowContract、WorkflowController、证据引用和独立 Reviewer 门槛 | 不新增调度器、Ticket 或验收账本 |
| 反馈转改进 | ImprovementStore、Capability Radar、initiative_feedback_bridge | 反馈保持未验证信号，需研究和审批 |
| 用户目标与行动反馈 | WorkflowContract 已有 goal、desired_outcome、acceptance_criteria、scope、exclusions、escalation_conditions、feedback_metrics | 可在现有契约上做小幅扩展，先验证调用方兼容性 |

本轮读取的关键实现：src/muse/research_knowledge.py、src/muse/inspiration.py、src/muse/inspiration_evolution.py、src/muse/memory_reflection.py、src/muse/initiative_feedback_bridge.py、src/muse/workflow_profiles.py；并核对 docs/VISION.md、docs/CAPABILITY_BACKLOG.md、docs/PROGRESS.md 最新记录。

## 5. 真正值得补齐的差异

建议不把上游内容搬进来，而是独立建立 Muse 的「情境理解与行动实验」能力切片，优先解决以下缺口：

1. **来源分层**：逐项区分用户原话、可观察事实、第三方陈述、系统记录、解释性推断；每项保留来源和时间，不把推断写成用户事实。
2. **多假设并列**：对同一现象保留多个可竞争解释，记录支持点、反面线索、缺失信息、置信度和可推翻条件；不允许单一隐性动机成为默认答案。
3. **参与者视角**：记录不同参与者自己表达的观点；未被本人表达的心理状态必须标记 unknown/hypothesis，不替别人读心。
4. **范围与安全分流**：一般成长/沟通/产品研究与临床、危机、法律或其他专业事项分开；高风险信号优先安全处置，不用普通自助流程拖延专业支持。
5. **最小可逆行动**：建议必须有执行者、期限、资源限制、预期观察信号和停止条件；不把对方配合或购买作为唯一成功指标。
6. **结果回访**：回访实际结果、意外副作用和当事人反馈；结果不佳时允许否定原假设，而不是强行解释成用户尚未理解。
7. **隐私优先**：个案/关系内容默认不自动写入长期 User Track，不上传云端；只有用户明确选择保存时才进入本地受控记录，并支持撤回/软删除及来源追溯。

## 6. 建议的融合架构（复用现有底座）

建议以一个无外部依赖的纯数据契约/服务层起步，暂定内部名称 contextual_inquiry（名称可再评审）：

- 输入：用户目标、场景、用户明确提供的观察、参与者列表、限制与安全提示。
- 输出：observations[]、interpretations[]、alternative_hypotheses[]、unknowns[]、stakeholder_views[]、action_experiments[]、safety_route。
- 每个解释必须关联至少一项来源或明确标记为无来源假设；同时允许反证和替代解释。
- 不负责保存长期记忆、不负责检索网页、不负责派发任务；分别调用现有 MemoryStore、ResearchKnowledgeStore、TaskQueue/WorkflowController。
- 将经过用户同意的研究问题转成 Research Wiki idea/claim；外部证据由现有 ResearchPipeline/AutonomousResearchEngine 采集。
- 将行动实验映射为现有 WorkflowContract 的验收标准与反馈指标；完成必须提供可核验的结果证据。
- 由 Muse Commander 决定是否建 Ticket、是否授权执行；模块本身没有调度权、网络权或任意工具执行权。

## 7. 安全与质量验收

首版至少需要以下测试：

- 单一行为描述不能直接生成确定的心理动机。
- 同一现象可以保留两个以上竞争解释；缺少证据时置信度不能被抬高。
- 用户原话与模型推断在序列化、检索和 UI 展示中始终分开。
- 未经本人表达，不把第三方心理状态写成事实。
- 用户拒绝保存时，不创建长期记忆记录。
- 危机/超范围输入触发安全分流，不继续普通行动实验。
- 行动计划必须可逆、可观察、有期限及停止条件。
- 结果反馈可以推翻原假设，不能自动升级成事实或自动修改政策。
- 使用现有 TaskQueue/ResearchKnowledgeStore；不得新增第二个 Commander、调度器或知识账本。

## 8. 实施顺序

- Phase 0（本轮）：上游身份、文件、许可、能力重叠与差异评估；完成只读架构检查。
- Phase 1：独立定义 contextual_inquiry 数据契约及纯函数校验；只创建临时数据，不接触真实用户个案。
- Phase 2：为契约增加单元测试和安全/隐私测试；通过后再评估与 WorkflowContract 的最小适配。
- Phase 3：在本地合成样本上进行端到端演练；验证 Research Wiki 引用、替代假设、行动反馈和审批链。
- Phase 4：通过独立 Reviewer 与回归后，才考虑进入桌面 UI；默认关闭、用户主动启用。
- 商业授权未取得前，所有实现均保持原创，不携带上游受限内容。

## 9. 本轮状态

- 上游仓库及四份关键文件已成功读取。
- Muse 相关架构与主要实现已只读检查。
- 已确认：概念层有参考价值；直接软件复用价值低；许可不适合未经授权的商业内容集成。
- 本轮尚未修改 Muse 源码、安装上游 Skill、复制上游公式、执行第三方代码或运行用户个案。
- 下一步：在确认当前并行工作窗口不会修改同一目标后，按 Phase 1 开发原创数据契约与测试。
