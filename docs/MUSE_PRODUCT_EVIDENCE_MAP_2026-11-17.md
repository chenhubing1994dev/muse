# Muse 产品介绍册证据映射（2026-11-17）

> 版本：0.1  
> 用途：第二版产品介绍册事实核验底稿  
> 原则：代码存在 ≠ 端到端验收；测试存在 ≠ 本轮已执行；测试夹具 ≠ 真实作品。

## 1. 证据状态定义

- **代码可定位**：存在具体实现文件，可供审查；不自动代表运行成功。
- **测试有覆盖**：存在针对性测试文件/用例；本文件不把未执行的测试写成通过。
- **历史实测记录**：项目文档记录了实际运行结果；仍需确认记录对应的工件、版本与当前环境。
- **端到端待验收**：链路尚缺真实场景、独立质量评估或完整发布门禁。

## 2. 能力主张映射

| 介绍册主张 | 可定位实现/记录 | 测试/工件线索 | 当前可公开成熟度 | 限制与禁止表述 |
|---|---|---|---|---|
| Muse 以 Commander 组织任务规划 | `src/muse/commander_planner.py`；`docs/AGENT_COMMANDER.md` | `tests/test_commander_planner.py`；`tests/test_unified_runtime.py` | 代码可定位，测试有覆盖 | 不等于所有任务都由统一中枢完整接管，也不等于所有 Agent 在线 |
| 专家路由与角色能力约束 | `docs/AGENT_COMMANDER.md`：SpecialistRegistry、SpecialistRouter、allowlist、显式 adapter 注册 | `tests/test_specialist_router.py`；`tests/test_agent_registry.py` | 已有实现/测试覆盖 | 当前路由以确定性词汇/短语匹配为主；未连接的内置模板只是元数据，不是可执行供应商 Agent |
| DAG 计划与安全回退 | `src/muse/commander_planner.py`：角色约束、依赖校验、safe fallback、并行 wave | `tests/test_commander_planner.py` | 已有实现/测试覆盖 | 32 是配置/架构上限，不代表已跑满 32 路；模型规划输出仍需校验 |
| 独立审查门禁 | `docs/AGENT_COMMANDER.md`；审查实现位于 `src/muse/review_gate.py` | `tests/test_review_gate.py` | 已有实现/测试覆盖 | 高危发现可否决；审查器不是自动正确性证明，也不代表每个产物都已走过审查 |
| Research Wiki / 研究知识图谱 | `src/muse/research_knowledge.py`：source、idea、experiment、claim、decision、failure 节点；普通关系边与独立的 claim_evidence / claim_evidence_reviews / source_relationships 表 | `tests/test_research_knowledge.py`；`tests/test_experiment_bridge.py` | 持久化图谱和证据审查数据模型已实现；当前数据库快照显示审查/来源关系数据尚未落地 | supports/refutes 是可用的关系/审查语义，不代表当前库已有记录；当前快照 claim_evidence_reviews 与 source_relationships 均为 0 |
| 证据质量、反证与来源独立性 | `src/muse/research_knowledge.py`：evidence quality、independence、review verdict、append-only 记录 | `tests/test_research_knowledge.py` | 已有数据模型与部分工作流 | 不能把候选证据直接当成已验证事实；每个具体主张仍需来源审查 |
| 自主研究与改进提案 | `src/muse/autonomous_research.py`、`src/muse/evolution_executor.py`、`src/muse/improvement.py` | `tests/test_autonomous_research.py`、`tests/test_evolution_executor.py`、`tests/test_improvement.py` | 代码与测试入口可定位 | 不应宣称无限自主进化或无需审查的自动改写；升级需独立审查、验证与授权 |
| Watchdog 与任务健康检查 | `scripts/muse-watchdog.cmd`；`src/muse/watchdog.py`；`src/muse/daemon_control.py` | `tests/test_watchdog.py`、`tests/test_autonomous_runtime.py` | 已有检查、报告与生命周期控制实现 | Watchdog 的健康报告不等于后台服务长期稳定；需真实运行时长、故障注入和恢复记录 |
| 粤语本地 TTS | `src/muse/cantonese_tts.py`；`docs/MUSE_VIDEO_NARRATION.md` | `tests/test_cantonese_tts.py`；Narration 文档记录离线真实合成样本 | 有真实本地合成记录与回归覆盖 | 当前模型为中性默认音色；不得宣传为可选成熟御姐女声或自然度已通过人工验收 |
| 粤语 ASR、SRT 与语音对稿 | `src/muse/cantonese_asr.py`；`docs/MUSE_VIDEO_NARRATION.md` | `tests/test_cantonese_asr.py`；Narration 文档记录 CER 样本与待复核项 | 有本地推理/字幕能力记录，质量验收未完成 | 机器 CER 不等于听感、口音、专名、时轴与编辑质量；人工试听仍是必要门槛 |
| 旁白混音与产品打包 | `docs/MUSE_VIDEO_NARRATION.md`；`src/muse/video_product_builder.py` | Narration 文档记录 FFmpeg 测试夹具 E2E、ZIP 与哈希验证；针对性测试包括 `tests/test_video_product_builder.py` | 工具链有真实 smoke-test 记录 | 合成测试画面不是 Wan AI 镜头；不得把 smoke test 宣称为正式成片 |
| Wan 2.1 视频生成与商业化闭环 | `scripts/run_wan_gpu_acceptance.py`、`docs/COMMERCIAL_VIDEO_PILOT_20261114.md`、`docs/MUSE_HIGH_VALUE_VIDEO_FACTORY.md` | 需逐项核验真实 MP4、manifest、QA、素材授权与发布记录 | 当前介绍稿记载的 24.5 秒短片处于 testing | 该案例复用已有 Wan 镜头并由 FFmpeg 包装；该轮未重新运行 Wan 推理、无旁白、未发布、无收入证据 |

## 3. Research Wiki 当前数据库只读快照（2026-11-17）

读取 `data/research/knowledge.sqlite3`，使用 SQLite `mode=ro` 只读连接；未调用会写入 schema 或数据的 Store 初始化方法。

- 数据库文件：8,486,912 bytes。
- 活跃节点：910（idea 302、claim 252、source 174、experiment 167、failure 15；decision 当前为 0）。
- 节点状态：active 589、proposed 315、registered 3、succeeded 3。
- 关系边：2,459（relates 1,568、inspired_by 417、addresses_gap 274、tested_by 164、alternative 36）。当前快照没有 supports/refutes/contradicts 等审查结论边记录。
- 事件：5,543；悬空或指向已删除节点的边：0。
- 孤立活跃节点：156。
- claim_evidence：281 条；有证据记录的 claim 103 个；没有 claim_evidence 记录的 claim 149 个。
- claim_evidence_reviews：0 条；source_relationships：0 条。
- 已进入 supported/refuted/contested/superseded 状态、但缺 reviewer 或 evidence_refs 的 claim：0 个。这一指标只覆盖已进入这些状态的 claim，不能推导所有 claim 都已审查。

**解读：** 图谱已经积累了相当规模的节点、关系与事件，但证据审查与来源独立性数据明显不足。现阶段对外应称为“已具备研究知识图谱与证据审查机制”，不能称“所有主张均经过独立审查”或“证据关系已经完整闭环”。孤立节点及未附证据的 claim 应成为下一轮 Research Wiki 数据治理任务。

## 4. 当前最稳妥的对外表述

Muse 已具备本地任务编排、专家路由基础、受约束的 DAG 规划、独立审查门禁、持久化研究知识图谱，以及粤语语音合成和视频产品打包的部分真实链路。产品仍在持续开发；不同模块的验收深度并不相同。视频旁白、真实 Wan 镜头的完整交付、长期无人值守运行，以及发布后的数据与收益回流，仍需逐项验收。

## 5. 发布前必须补齐

1. **本轮目标测试已执行：8 个测试文件、140 项测试全部通过，0 项失败。** 运行环境为项目 `.venv` 的 Python 3.11.9，使用标准库 `unittest discover`；逐文件结果：CommanderPlanner 14、SpecialistRouter 16、ReviewGate 13、ResearchKnowledge 37、Watchdog 18、Cantonese TTS 4、Cantonese ASR 8、VideoProductBuilder 30。执行命令逐文件返回 exit code 0。
2. Runner 的结构化 pytest 验证入口未成功：环境没有安装 pytest；这不影响上述 unittest 结果，但不能将本轮描述为 pytest 验证通过。
3. **第二批扩展测试：7 个文件均 exit code 0。** AutonomousResearch 52、EvolutionExecutor 主测试集 13、Improvement 13、AgentRegistry 9、UnifiedRuntime 41、ExperimentBridge 9、AutonomousRuntime 18。EvolutionExecutor 测试运行期间另有两个独立的单测试辅助进程输出；此处按主测试集报告，不把辅助进程重复计入总数。
4. 为 24.5 秒短片建立可公开工件清单：MP4、manifest、QA 报告、工程 ZIP、镜头来源、素材许可、生成日期和版本。
5. 完成真实 Wan 镜头 + 粤语旁白 + SRT 的完整 E2E；人工试听并审查字幕同步、响度、混音、画面质量与披露。
6. 对 Research Wiki 的 27 个 lint 同口径孤立节点、149 个无 claim_evidence 的 proposed claim、103 个已有但未审查候选的 claim，以及 5 条来源身份候选开展分批治理；不要自动把候选证据升级为事实。7. 记录 Watchdog 连续运行时长、故障注入、租约过期、恢复行为和告警证据。
8. 对外发布前完成第三方素材、字体、音乐、商标与合成语音披露审查。

## 6. Research Wiki 数据治理复核（同日追加）

- 156 个孤立节点为全节点宽口径；代码 `ResearchKnowledgeStore.lint()` 仅检查 idea/experiment/claim，按同口径实际为 27 个：14 个 proposed claim、13 个 active idea。source/failure 等节点不能只因无普通关系边就视为异常。
- 149 个无 `claim_evidence` 的 claim 全部为 proposed；103 个已有候选证据的 claim，其候选均尚无 `claim_evidence_reviews`。高级 claim 状态当前为 0。
- `source_identity_candidates` 有 5 条待审候选（canonical_equivalent 3、same_author 1、same_publisher 1），不是已确认来源关系。
- 详细分级治理路线见 `docs/RESEARCH_WIKI_DATA_GOVERNANCE_2026-11-17.md`。本轮仍为只读审计，没有写入数据库或修改应用代码。

## 7. 只读审计工具已落地（2026-11-17）

- 新增 `src/muse/research_wiki_audit.py`，提供只读 Research Wiki 审计与 JSON 治理队列；命令注册为 `muse-research-wiki-audit`。
- 使用 SQLite `mode=ro` + `PRAGMA query_only=ON`，不调用会初始化 schema 的 Store 构造器；不输出节点标题或正文，不写入知识库。
- 生产库实跑摘要：910 活跃节点、2,459 活跃边、27 个 lint 同口径孤立节点、149 个无证据 claim、281 条未审查证据候选、0 条来源关系、5 条待审来源身份候选、0 个高级状态 claim。注意：281 是证据候选条目数，不是 claim 数；它们分布于 103 个 claim。
- 命令实跑成功，`--limit 3` 生成 12 条示例治理队列记录（每类最多 3 条）。新增单元测试待执行。

## 7. 本轮工作边界
本文件基于本轮读取到的实现文件、测试文件和项目文档建立。本轮通过项目 `.venv` 的 Python 3.11.9 标准库 unittest 执行两批目标测试：第一批 8 个文件、140 项测试全部通过；第二批 7 个文件均 exit code 0，主测试集分别报告 AutonomousResearch 52、EvolutionExecutor 13、Improvement 13、AgentRegistry 9、UnifiedRuntime 41、ExperimentBridge 9、AutonomousRuntime 18。结构化 pytest 验证未运行成功，原因是环境缺少 pytest。没有重新生成视频或语音成品；测试通过不等于真实 Wan + 粤语旁白成片已完成验收。Research Wiki 数字来自本轮对 `data/research/knowledge.sqlite3` 的只读快照。项目其他未读取文件的状态不在本文件断言范围内。