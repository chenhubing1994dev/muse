# Muse 执行计划：受控实验闭环与四体系灵感引擎

更新日期：2026-11-10  
状态：持续执行中；本文件记录已核实现状、实施顺序与验收边界。

## 目标与总顺序

先完成“自主研究 → 假设 → 受控实验/证据核对 → Research Wiki → 独立审查 → 改进提案”的可追溯闭环，再深化易经、奇门遁甲、紫微斗数、大六壬四体系灵感引擎，最后整合自主进化与后台可靠性。

Muse Commander 是唯一调度与授权中心。不得另建第二套调度器、Ticket 队列或审批权威；优先复用现有 TaskQueue、ResearchPipeline、ResearchKnowledgeStore、ExperimentBridge、TestRunner、ImprovementStore 与 IndependentReviewGate。

## 已核实的现有基础

- ResearchPipeline 已执行支持方向与反证方向检索，并保存来源快照、引用 ID、SHA-256、来源差异与反证候选。
- ResearchKnowledgeStore 已支持 Idea、Claim、Experiment、Failure 节点及 supports/refutes/related/alternative/tested_by 等知识关系。
- B-073 将 ResearchPipeline 的 source-retrieval run 与自主研究主 Idea/Claim 通过 Research Wiki 关系连接；明确标注它是来源检索观察而非现实实验或 claim verdict。

- `record_classified_failure_experiment()` 已支持 supports/refutes/inconclusive 分类、稳定 run ID、幂等复用与反证记录。
- Improvement proposal 状态已由 CapabilityRadar 扫描同步至 Research Wiki；该同步为扫描驱动的最终一致性，不是跨 SQLite 原子双写。
- B-062 已有固定白名单 `feedback_research_lifecycle` 实验：只运行代码登记的测试文件，源代码与测试复制到临时快照，使用隔离 TestRunner、超时与内存限制，并把 manifest/run 关联到研究计划；未知 experiment key 会被拒绝。
- B-063 已有可选本地第二模型 Reviewer 装配；若未配置真正不同的 Reviewer 模型，状态必须保持 pending/unavailable，不得自审冒充独立审查。
- 2026-11-10 已新增四个核心术数 profile 与跨体系比较 API；目前是结构化知识/问题框架，不是完整排盘器，profile 的 source_refs 仍为空。

## P0：完成自主研究与受控实验闭环

### P0.1 明确两类不同的“实验”
1. 工程回归实验：运行 Muse 自身的白名单测试套件，只能证明软件流程/代码行为，不是外部事实证据。
2. 研究假设验证：使用预先登记的现实观察指标、来源、实验设计和反证条件；不能把普通 unittest 通过解释为科学主张成立。

两类结果必须使用不同 execution_class 与 epistemic_status，禁止混淆。

### P0.2 扩展安全的实验注册目录
- 只允许代码维护者登记的 experiment_key、固定测试套件/固定 harness、参数 schema、超时、内存上限和允许的输出字段。
- 模型输出、用户文本、研究标题不能成为文件路径、pytest/unittest pattern、命令或 argv。
- 运行前校验研究计划与 hypothesis/claim 的精确关联、状态及未执行标记。
- 使用源码指纹和稳定 run_id；相同快照重复调用幂等复用，快照变化则产生新 run。
- 只在临时快照执行；排除私人数据、凭证、配置、模型文件与 symlink。
- 零测试、超时、异常、非零退出码、缺少证据记录均不能算成功。

### P0.3 研究证据闭环
- 研究计划预先登记 hypothesis、support query、counter query、成功标准、反证标准、替代解释与停止条件。
- 将实际来源快照、citation ID、SHA-256、实验 manifest/run、测试指标和时间戳关联到 Research Wiki。
- 证据不足、来源不可读、来源依赖未明或 Reviewer 不可用时保持 inconclusive/pending，不强制分类。
- supports/refutes 只能在适用的预登记规则和独立审查满足后记录；检索方向本身不构成 verdict。
- 保留失败实验和负结果；失败的检索/软件运行不等于研究假设被反驳。
- 评估跨 SQLite outbox/补偿同步，确保 ImprovementStore 与 ResearchKnowledgeStore 最终一致且可恢复。

### P0.4 独立审查与改进交接
- Reviewer 必须具有可验证的独立身份；Builder 自审或同模型复述不算独立审查。
- Reviewer 不可用时保留 pending，不阻断资料沉淀，但不批准代码改动。
- 提案者不得批准自己的改动。
- 正式代码修改仍需人类批准、隔离执行、基线/候选测试、独立审查、回滚证据。

## P1：四大术数知识与灵感引擎

### P1.1 规则知识库
分别维护：
- 易经：经传/注本、卦象、爻位、卦变及采用的起卦方法。
- 奇门遁甲：历法换算、阴阳遁/局数、九宫、天地人神盘、门星神、值符值使及流派规则。
- 紫微斗数：历法与闰月、时间/时区校正、十二宫、星曜、四化、大限/流年/流月及流派规则。
- 大六壬：月将、占时、四课、三传、天将、课体及取传流派规则。

每条规则必须记录来源、版本/版次、作者或整理者、适用流派、原文/页码或章节、录入时间、校验状态和差异说明。无来源的规则标为 unverified，不得补造古籍出处。
- 开源组件初步尽调见 docs/METAPHYSICS_ENGINE_RESEARCH.md：I Ching Research Skill、Zi Wei PyPI ziwei、Da Liu Ren kinliuren 已评估；dglijin Qimen 候选因 README 历法矛盾未接入，改用隔离的 qimen-rs 0.2.1 时家拆补 Provider。


### P1.2 确定性排盘与测试
- 已有确定性易经三钱结构计算器与收到本序六十四卦结构目录，支持可见 seed/用户六爻输入、本卦/之卦、互卦/错卦/综卦；目录是二级结构交叉核对，不含经传文本或断语。
- 紫微斗数上游 0.4.1 已在独立 D 盘 venv 完成合成命盘 POC，并接入 ZiweiProvider；显式要求性别/闰月/四化口径，输出不含原始出生记录。知识目录仍通过适配器接入 Research Wiki；未作为 Muse 必需依赖，详见 docs/ZIWEI_POC.md。
- 大六壬 Kinliuren 0.1.2.9 与奇门 qimen-rs 0.2.1 已在独立 venv 实际运行并接入 Provider；Kinliuren 仍缺独立黄金课例，QiMen 仅覆盖时家拆补转盘且 wheel 未源码审计。详见 docs/KINLIUREN_POC.md 与 docs/QIMEN_POC.md。
- 已实现 CalendarPolicy/CalendarContext、celestial-calendar Provider 与 Muse sexagenary v1；Ziwei/QiMen/Kinliuren 三套实际排盘 Provider 已接入 InspirationEngine，统一调度逐个执行、逐个报告不兼容状态。仍待历史盘差分、DST/闰月/真太阳时边界和四体系解释/反证闭环；详见 docs/CALENDAR_CONTEXT.md。
- build_metaphysics_chart_dossier() 已把同一历法 fingerprint 下的 2–3 套实际盘面与可选 Yi Jing cast 合成四体系研究档案，提炼结构观察、chart hash 和可证伪研究问题；候选仅为框架层类比，不声称直接解读盘面；不强求共识、不生成断语、不持久化个人盘。


- 先选择有可靠文本依据、可明确编码的一种流派作为首个实现，不把不同门派规则混算。
- 历法、时区、节气、闰月、边界时间、夏令时和地点参数分别建立测试向量。
- 排盘结果必须输出所用规则版本、全部输入参数、来源、六爻值、结构映射与可复算中间步骤；二级目录不可标记为已校勘古籍文本。
- 未提供必要输入时只给出象征性问题，不得声称已起卦/排盘。
- 紫微等需要出生资料的功能遵循最小化收集、本地优先和用户主动提供原则。

### P1.3 多体系交叉分析
- 比较时间变化、结构位置、过程依赖、角色资源四个维度。
- 保留体系之间的共通问题、不同解释、冲突和不可比参数。
- 交叉相似性只是灵感线索，不等于流派共识或现实证据。
- 每个综合候选必须给出 test_question、falsification_rule、alternative_explanations、source_refs 和 uncertainty。
- 实际排盘结构只能作为 symbolic inspiration；必须附规则版本和来源，缺独立原文/盘例时 agreement_status 保持 not_assessed，不能当作证据。
- 独立 Reviewer 的结果已写入 Research Wiki decision 节点，并关联 experiment run/plan；通过仅代表工程证据审查，不是外部 Claim 事实认定或代码变更批准。

- 产生软件设计、研究主题、产品原型等创造候选，并回写 Research Wiki；不得自动写入 User Track 事实记忆。

## P2：自主创造与哲学进化
- 从创造者已批准的书签、兴趣和历史研究发现新主题。
- 让象征灵感生成跨领域假设和创造候选。
- 保存成功、失败、偏差、反例与用户反馈。
- 哲学原则可提出版本化修订候选；旧原则保留历史，不直接覆盖。
- 把高价值候选转为可体验的原型/工具/功能，执行测试与审查后交付。

## P3：后台可靠性与主动汇报
- 继续完善 Commander 的任务租约、心跳、取消、暂停/恢复、崩溃回收与幂等。
- 按真实 CPU/RAM/Runner/Provider 约束并发；32 个 slots 不代表 32 个真实 worker。
- 保留可观察状态、进度事件、失败分类、资源预算、停止控制和主动汇报。
- Windows 常驻服务/开机自启需单独评估和明确授权，不因开发计划自动启用。

## 每阶段验收要求

- 先写失败测试，再实现。
- 变更前读取文件与 revision；非 Git 工作区不以 Git 作为安全边界。
- 定向测试、完整回归、compileall 与源码审查均需执行。
- 测试结果记录真实运行数、通过/失败/跳过、退出码与时间。
- 非 Git source freshness 未被工具证明时，保留 unproven 警告；不把命令退出码 0 夸大成完整审计通过。
- 更新 `docs/PROGRESS.md`，明确区分已实现、待实现、受限和未验证。

## 当前阶段快照（2026-11-10）

- Runner 已恢复，原 Muse Project/Session 已接回。
- 新增四体系结构化 profile、cross-system inspiration API，以及确定性易经三钱结构计算器；均接入 Muse 灵感入口或自主研究 Idea metadata/报告。
- 将来源检索 run 关联到本轮研究 Idea/Claim；该 run 明确不是现实实验结果或 claim verdict。
- 定向测试：`test_inspiration.py` 17/17；`test_yijing.py` 5/5；`test_autonomous_research.py` 44/44；`test_feedback_experiments.py` 5/5。
- 完整 unittest：861 项运行，0 failures、0 errors（72.103 秒）；compileall 成功。
- 仍未完成：通用领域受控实验/harness 执行器与现实实验验证；四体系可引用规则语料与确定性排盘器；跨数据库原子 outbox；真实独立 Reviewer 部署；长期后台服务。
