# Muse 品牌资料进度补记（2026-11-17）

## 已核实

- `artifacts/brand/MUSE_BRAND_BROCHURE.pdf` 存在，1,059,077 bytes，SHA-256：`4b5669318c14d810725b2512b9feca4da93e9dd1f63365db77747763c1edb7bf`。
- PDF 的既有生成记录为 5 页 A4；本轮重新读取项目工件元数据，确认文件存在、大小与哈希一致。此处未重新执行逐页视觉检查。
- `artifacts/brand/MUSE_BRAND_COVER.png` 存在，1600×2263，424,826 bytes，SHA-256：`8c2dc13087a7de07ad3af3750a72ab595be4d13aace9e76672ee97bdf541d185`。
- 可编辑文案、HTML 与事实核验表存在于 `artifacts/brand/source/`。
- `artifacts/brand/share/` 目录目前为空；此前在 ChatGPT 会话中生成的社交海报不属于已同步的项目文件。
- 宣传册文案已清楚标注视频试产仍处 testing、没有旁白/发布/收入证明；粤语 TTS 的真实合成记录与语音质量验收也明确分开。

## 本轮新增

- 更新 `docs/MUSE_BRAND_BROCHURE_PLAN.md`：明确第一版状态、分享目录状态，并新增第二版产品介绍册的页面结构与事实边界。
- 新增本文件，作为品牌资料专项进度补记。由于 `docs/PROGRESS.md` 为大型历史文件，读取时出现字符集显示异常，本轮不直接改写该文件，避免破坏历史编码与换行。
- 新增 `docs/MUSE_PRODUCT_EVIDENCE_MAP_2026-11-17.md`，将产品主张映射到代码、测试、历史实测与未验收项。
- 本轮实际执行 8 个核心测试文件，使用项目 `.venv` Python 3.11.9 的 `unittest`，共 140 项全部通过（0 失败）：CommanderPlanner 14、SpecialistRouter 16、ReviewGate 13、ResearchKnowledge 37、Watchdog 18、粤语 TTS 4、粤语 ASR 8、VideoProductBuilder 30。pytest 未安装，结构化 pytest 入口未通过；不影响上述 unittest 结果。
- 扩展验证另执行 7 个测试文件，全部 exit code 0：AutonomousResearch 52、EvolutionExecutor 主测试集 13、Improvement 13、AgentRegistry 9、UnifiedRuntime 41、ExperimentBridge 9、AutonomousRuntime 18。EvolutionExecutor 运行时另有两个独立辅助进程各输出 1 项测试，不重复计入主测试集。
- 对 `data/research/knowledge.sqlite3` 做只读快照：910 个活跃节点、2,459 条关系边、5,543 个事件；156 个孤立节点，149 个 claim 没有 claim_evidence，证据审查记录 0 条、来源关系记录 0 条。数据已写入证据映射，明确列为下一轮知识治理任务，不包装成“研究闭环已完成”。

## 下一步

1. 对第一版 PDF 做完整逐页视觉检查，并核验字体、断行、页脚、图片裁切与可搜索文本。
2. 将社交海报最终稿通过受支持的项目文件导入路径同步到 `artifacts/brand/share/`；同步前后核对文件大小与 SHA-256。
3. 完成第二版产品介绍册完整文案审校，再制作 6–8 页 PDF；证据映射已建立，需把 Research Wiki 当前数据缺口如实写入产品成熟度页。
4. 真实 Wan 镜头 + 粤语旁白 + SRT 的完整 E2E 仍须单独执行并人工试听；单元测试通过不代表真实成片已验收。
5. 所有发布稿必须明确区分：已验证、已实现但未完成端到端验收、开发中、规划中。
## Research Wiki 治理审计补记（2026-11-17）

- 新增 `docs/RESEARCH_WIKI_DATA_GOVERNANCE_2026-11-17.md`，把孤立节点、未附证据 claim、未审查候选与来源身份候选拆成独立治理队列。
- 复核 `ResearchKnowledgeStore.lint()` 后纠正统计口径：156 是全部节点的宽口径孤立数；lint 实际只检查 idea/experiment/claim，同口径为 27（14 个 proposed claim、13 个 active idea）。
- 149 个无 claim_evidence 的 claim 全部为 proposed；103 个已有候选证据的 claim 尚无证据审查记录；高级 claim 状态为 0。来源身份候选 5 条仍待审，不视为已确认关系。
- 本轮仅做 SQLite `mode=ro` 审计和文档新增/补记，没有修改生产数据库或应用源代码。

## Research Wiki 审计 CLI 开发补记（2026-11-17）

- 新增 `src/muse/research_wiki_audit.py`，注册 `muse-research-wiki-audit` 命令；SQLite `mode=ro` + `PRAGMA query_only=ON`，不会初始化或写入生产知识库。
- 支持输出审计摘要与 JSON 治理队列，默认不输出节点标题或正文；可选 `--output` 保存报告文件。
- 生产库实跑：910 活跃节点、2,459 活跃边、27 个 lint 同口径孤立节点、149 个无证据 claim、281 条未审查证据候选（涉及 103 个 claim）、0 条来源关系、5 条待审来源身份候选、0 个高级状态 claim。
- 新增 `tests/test_research_wiki_audit.py` 两项测试通过；与 `tests.test_research_knowledge` 联合回归共 39 项测试通过；`compileall` 成功。
- 本轮新增审计模块与命令入口，未修改原有知识图谱写入逻辑，未写生产数据库。

## Research Wiki 人工证据审查工作台补记（2026-11-17）

- 新增 `src/muse/research_wiki_review.py`，注册 `muse-research-wiki-review`；以只读 SQLite 连接输出待审证据候选的主张、来源标题/链接、检索方向、类型、备注及审查状态。
- 支持 `--claim-id`、`--include-reviewed`、`--limit`、`--output`；不抓取来源正文、不伪造人工 reviewer/verdict、不修改 claim 状态、不写生产数据库。
- 生产库 `--limit 2` 只读冒烟测试成功，实际展示两条待审候选及其主张/来源溯源字段。
- 修复并重整 `pyproject.toml` 中粘连的脚本入口，保留既有脚本并分别注册 audit/review 两个 CLI；Python `tomllib` 校验通过。
- `tests.test_research_knowledge`、`tests.test_research_wiki_audit`、`tests.test_research_wiki_review` 联合回归：39 项通过；两个 CLI 模块 compileall 通过。
- 生产知识库保持只读，未执行任何真实证据裁决。下一步：在审查队列基础上实现显式人工审查命令，并增加来源全文快照/引用核验和同源转载识别门禁。

## Research Wiki 显式人工审查写入 CLI 补记（2026-11-18）

- 新增 `src/muse/research_wiki_review_record.py`，注册 `muse-research-wiki-review-record`。
- 必须明确提供 evidence/reviewer/verdict/quality/independence/rationale，并使用 `--confirm-write` 才执行追加式审查；复用既有角色独立性校验，不修改 claim 状态。
- 新增两项临时数据库测试：未显式确认拒绝写入；确认后审查记录追加成功、claim 仍保持 proposed；拒绝无效 verdict 和作者自审。
- 联合回归现为 41 项通过；TOML 入口注册与 compileall 通过。
- 仅在临时测试库验证写入，不对生产知识库执行审查写入。来源正文快照/引用定位尚未实现，审查 CLI 不应被理解为自动核验工具。



## Research Wiki 来源引用锚点核验补记（2026-11-18）

- 新增 `src/muse/research_wiki_citation.py` 与 CLI `muse-research-wiki-citation`，直接复用现有 `ResearchEvidenceStore` 的来源快照，不复制正文、不重新抓取。
- 引用核验只读打开 evidence.sqlite3，校验 URL + 全文 SHA-256 + 精确唯一摘录，输出 start/end 字符偏移、摘录 SHA-256、content type 与 fetched_at；缺失、篡改、截断、重复或不匹配均拒绝。
- 新增 `tests/test_research_wiki_citation.py` 3 项测试，覆盖有效偏移/只读、错误哈希/摘录缺失/重复摘录、截断/缺失快照。
- 五组联合回归（ResearchKnowledge、audit、review queue、review writer、citation）共 44 项通过；TOML 脚本入口校验与 compileall 通过。
- 仍未将引用锚点结构化关联到 `claim_evidence_reviews`，故支持/反驳写入目前尚未强制引用核验；此项列为下一步。生产 knowledge.sqlite3 与 evidence.sqlite3 均未写入。


## Research Wiki 审查引用强制绑定（2026-11-18）

- 在知识库新增 append-only `claim_evidence_review_citations`，review_id 外键绑定审查记录；存储 URL、全文/摘录 SHA-256、精确摘录、字符偏移、抓取时间和内容类型，禁止更新/删除。
- `record_claim_evidence_review()` 对 supports/refutes 强制要求引用锚点，并校验来源 URL 与对应 source node 完全一致；review 与 citation 在同一 SQLite 事务写入。
- 升级 review writer：supports/refutes 必须提供来源快照 SHA-256 与精确摘录；先从知识库解析来源 URL，再只读核验 evidence.sqlite3 的完整哈希和唯一摘录，核验通过后才写入；mentions/insufficient 可无引用。
- claim dossier 已返回每条审查的 citations；未复制正文全文，也未重新抓取。
- 新增临时数据库集成测试 2 项；五组联合回归现为 46 项通过。生产知识库和证据库均未写入。新增表采用 IF NOT EXISTS 兼容创建，不改动既有 schema 表及旧审查记录。
- 后续仍需加强：数据库 API 当前以“已核验结果对象”作为内部参数，真正的跨库快照验证由受控 CLI writer 执行；需继续评估直接调用底层 API 的权限边界，并补齐完整旧库迁移演练。


## Research Wiki 证据冲突与独立来源配对（2026-11-18）

- `claim_evidence_coverage()` 新增跨来源裁决状态：support_only / refute_only / contested / no_directional_reviews；支持与反驳同时出现时明确要求人工解决，不以多数票裁决。
- 新增支持来源数、反驳来源数，以及 `explicit_independent_cross_verdict_pair_count`。仅已人工确认 `independent_of` 且两端分别有支持/反驳审查的来源对才计入独立交叉裁决。
- 测试场景包含一个转载依赖来源和一个独立官方数据源：系统将主张标记为 contested，独立跨裁决配对计数为 1；不自动修改 claim 状态，仍为 proposed。
- 五组联合回归 46 项通过；生产数据库未写入。来源身份候选与谱系既有能力已复用，未另建重复模型。

## Research Wiki 来源身份候选可解释性（2026-11-18）

- 来源候选证据现在保存具体匹配值：规范化 canonical URL、出版方名称、重合作者名、引用 / isBasedOn 命中的 URL；审查者无需只依赖抽象规则标签。
- 复用既有候选表、append-only 审查和人工接受后转正式关系的流程；没有新增 schema，也没有把候选自动升级为已确认关系。
- 历史候选比较上限仍为 500 条；只比较本轮来源与历史来源，不对历史来源彼此做全量推断。
- 六组联合回归 52 项通过，py_compile 通过；生产 knowledge.sqlite3 与 evidence.sqlite3 未写入。


## Research Wiki 跨研究批次精确正文匹配（2026-11-19）

- 新增只读 `find_exact_content_matches()`：将本轮来源与本轮其他来源、最多 500 条历史来源按正文 SHA-256 比对。
- 只接纳有效 64 位哈希且未截断的快照；输出 `exact_content_match_hints`，包含来源节点、规范化 URL 和匹配哈希。
- 这是重复内容提示，不等于来源独立性或事实验证；未更改来源关系 schema，也未写入正式来源关系。
- 七组联合回归 83 项通过，py_compile 通过；生产知识库与证据库未写入。

## Research Wiki 证据缺口驱动动态选题（2026-11-19）

- `_next_topic()` 复用既有 `claim_evidence_coverage()`，为已存在的证据冲突和缺少反面检索方向的主张生成下一轮研究选题。
- 选题包含原主张 ID、来源节点 ID、缺口类型和证据覆盖状态；稳定 key 防止同一主张缺口重复生成。
- 候选只提出下一步研究问题，不自动裁决冲突、不升级主张、不执行工程修改；公开查询不包含审查理由或私密反馈。
- 已修复缺口扫描在知识库连接关闭后执行、异常被静默跳过的问题；单项集成测试通过，七个相关测试模块联合回归 132 项通过（不含需外网域名解析的 review-record 集成用例），py_compile 通过。

## 哲学实验失败反馈驱动动态选题（2026-11-19）

- `AutonomousResearchEngine` 新增可配置 `philosophy_db`，默认指向工作区 `data/philosophy/muse.sqlite3`。
- 只读查询既有 `philosophy_experiments`，将 `refuted` / `inconclusive` 实验转成后续研究候选；保留实验 ID、原则 key、结果摘要和指标。
- 不创建/迁移哲学库，不修改实验结果或原则置信度；显式关闭只读 SQLite 连接。
- 新增失败实验候选集成测试，确认原始实验记录不变。联合回归 139 项通过（8 个测试模块）；py_compile 通过。

## 研究历史去重与新方向优先（2026-11-19）

- `_next_topic()` 复用既有自主研究节点中的 `research_queries`，对支持/反面检索词做大小写与空白标准化后建立查询对指纹。
- 已研究过的相同检索词对不再优先；先保障拒绝评审跟进与 `researching` 状态的反馈提案续跑，再在同优先级内考虑重复度。
- 显式 `preferred_key` 中断恢复仍优先返回原选题。新增回归覆盖新查询优先与实验失败跟进；8 个相关测试模块 140 项通过，py_compile 通过。

## 当前验收状态

- 第一版 PDF/封面文件存在性与元数据：已核实。
- 第一版完整逐页视觉复核：待完成。
- 项目内社交分享图：未完成（目录为空）。
- 第二版产品介绍册：页面规划、0.1 文案框架与证据映射已建立；完整文案审校、数据治理说明与排版未完成。
- 不宣称全自动多平台发布、稳定变现或机器主观意识。

## 研究实验结果 → 后续研究候选（2026-11-19）

- `capability_radar.scan_capabilities()` 现从最近时间窗的 Research Wiki 实验记录提取 `refutes`、`inconclusive`、`failed` 结果，输出最多 50 条 `research_followups`。
- 候选保留 `source_experiment_id`、`source_run_id`、`source_hypothesis_id` 与结果方向，标记 `candidate_only=true`、`engineering_proposal_created=false`；不生成工程提案、不改写实验或假设记录。
- 新增能力雷达回归测试：确认反驳型实验被保留为研究跟进候选，且原实验 metadata 不变。
- 验证：`py_compile` 通过；`tests.test_capability_radar`、`tests.test_improvement`、`tests.test_research_knowledge` 共 63 项通过（exit code 0）。详见 `docs/RESEARCH_TO_IMPROVEMENT_GATE_2026-11-19.md`。
