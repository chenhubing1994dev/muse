# Harness² 超融合评估与 Muse 适配方案

日期：2026-11-12；上游：https://github.com/google-research/harness2；许可证 Apache-2.0（third_party、基准和数据集另有条款）。

## 结论
建议吸收核心方法，不整仓移植，不让 Harness² 成为第二调度器。Muse Commander 仍是唯一任务、权限、预算和验收中心。先做只读快照与确定性本地原型，不引入其外部模型 Provider、基准数据集或执行器依赖。

## 值得吸收的机制
1. 任务级 Propose–Probe–Compose：运行基线，提出通用/领域候选，使用相同任务对候选进行探测，再依据行为差异组合；并行模式广泛探索，顺序模式逐轮深入。
2. 长期经验递归：反思每项变更 helped / hurt / no_effect / confounded，并把有证据支持的经验按领域存储，供后续任务复用。
3. 八类可编辑部件：system prompt、guardrails、memory、tool guidance、subagents、skills、scripts、plugins；映射到 Muse 的哲学/策略、权限、记忆、工具路由、Agent、技能、脚本和插件。
4. 候选树 schema/allowlist 验证、差异摘要、多样性检查；未变更字段继承基线。
5. 保存候选、执行日志、产物、比较结论与最终采用版本，证据不只放在模型上下文中。

## Muse 融合映射
- Propose → Commander + Improvement Planner：创建改进 Ticket，不直接写生产配置。
- Probe → 现有 Worker/实验执行器：隔离临时副本执行基线与候选。
- Compose → 独立 Reviewer/Composer：比较轨迹、测试与产物。
- Reflection → Evolution/Progress Event Store：记录效果标签和证据。
- Experience Library → Research Wiki/Knowledge Store：按领域检索已采纳经验、来源和反例。
- Editable tree → Muse 策略/权限/记忆/工具/Agent/技能/脚本/插件配置；先只读快照和 schema。
- Parallel/Sequential recursion → Commander 子任务；并发、预算、租约、超时、取消仍由 Commander 控制。

## 风险与不移植理由
- 上游面向 OpenCode/Codex substrate 和指定 benchmark；Muse 是 Windows、本地优先、已有 Commander/Worker。
- 上游 pyproject 要求 Python >=3.12,<3.14，文档以 Linux + Python 3.12/3.13 为主；Muse 目前 Python >=3.11，不为此升级运行时。
- 模型实验需要外部模型配置；Muse 不新增强制云端 API 或付费调用。
- 上游 proposer.py 的 Claude CLI 命令含 --dangerously-skip-permissions；README 说明 executor network unrestricted、Codex sandbox disabled。**禁止复制这种执行权限模式**。
- Issue #111 记录并行 rollout 共享工作目录/临时文件会污染实验比较。Muse Probe 必须隔离副本、临时目录与输入；不能隔离则顺序执行；证据混淆标为 confounded，默认保留基线。
- 上游 benchmark 数字是作者报告，不代表 Muse 已复现。

## 实施阶段
### H2-M1 数据模型与只读基线
定义 HarnessSnapshot、HarnessCandidate、ProbeRun、CompositionDecision、ExperienceLesson；关联 Evolution Cycle ID、Commander Ticket ID、领域、基线哈希、候选差异、环境摘要和证据引用。先做只读快照、diff/schema 校验与 JSON/Markdown 报告。
### H2-M2 确定性 Propose–Probe–Compose
使用确定性编辑器与现有测试任务；每个候选隔离副本，相同输入/依赖/测试；记录退出码、测试、时长、资源和副作用。样本不足或环境不一致时 inconclusive/confounded，保留基线。
### H2-M3 独立审查与经验库
Reviewer 与 proposer 分离；保存 helped、hurt、no_effect、confounded，关联证据、适用范围、反例和置信度。失败经验保留但不升级成通用规则。
### H2-M4 小范围自主试运行
仅对 Muse 自有工作区内可回滚的提示词、工具指引、技能元数据开放自动探测/采用。生产权限策略、凭证、网络外传、自动发布、删除和用户数据访问不得自我修改。采用前后回归，失败回滚，限制并发/预算/递归深度并提供暂停开关。

## 自主文明模式权限
默认自主：公开资料研究、只读检查、内部知识整理、Muse 自有工作区代码/测试/文档修改、隔离实验、可回滚本地产物。仍需单独授权：付费/有价额度、私人数据外传、公开发布、删除用户资料、修改系统安全设置、不可逆变更及第三方高影响操作。安全策略不能由被优化 Agent 自行放宽；无已验证免费额度时云端 Provider 关闭。

## 当前状态

- H2-M1 已在 Muse 内部实现为确定性数据模型与比较器，详见 `src/muse/harness_evolution.py`。具备显式 allowlist、SHA-256 快照/候选 diff、路径/敏感文件/符号链接拒绝、环境/输入指纹比较和保守的结果分类。
- H2-M2 文件系统阶段已实现：校验快照源文件无漂移、复制显式 allowlist 到独立临时目录、只在副本应用候选差异、生成所有权标记与哈希清单，并且只清理标记匹配且位于指定临时根的 Harness 自有目录。
- H2-M2 静态探针只对副本中的 Python 使用 ast.parse、对 JSON 使用 json.loads；不 import、不执行候选代码、不启动子进程、不调用 shell。静态检查结果是语法/格式证据，不是运行时性能或功能证据。
- 探针执行前重新核对副本文件清单和内容哈希；副本被篡改或出现白名单外文件时拒绝继续。
- 安全修正：`ProbeRun.isolated` 默认值为 False；普通副本/静态探针不会被误标为隔离执行。文件系统副本不是进程/网络/OS 沙箱；没有自动生成可信 ProbeRun，也没有自动 Compose。
- 当前 Harness 专项测试 **16/16 通过**；最近一次全量 unittest **1,066/1,066 通过**，`compileall -q src tests` 通过。
- 环境检查：Windows 11 专业工作站版 Build 26200，当前 Runner 账户具备管理员权限；Windows Sandbox Optional Feature 状态为 Disabled。未擅自启用系统功能或触发重启。
- 上游浅克隆 Job `wc_job_3C3FRGBcLbC7mFWw` 在 180 秒后超时；未安装、未运行上游代码，不能宣称 clone 完整。
- 下一步：评估并验证 Windows OS 级隔离机制（受限 Token/低权限账户、Job Object、AppContainer 或 Windows Sandbox）；没有可验证的强隔离之前，不运行任意候选代码，不生成可采纳的隔离 ProbeRun。

## 参考- Proposer: https://github.com/google-research/harness2/blob/main/harness2/core/proposer.py
- Recursion: https://github.com/google-research/harness2/blob/main/harness2/core/recursion.py
- Tree: https://github.com/google-research/harness2/blob/main/harness2/core/tree.py
- Library: https://github.com/google-research/harness2/blob/main/harness2/core/library.py
- Experiments: https://github.com/google-research/harness2/blob/main/docs/EXPERIMENTS.md
- Isolation issue: https://github.com/google-research/harness2/issues/111
