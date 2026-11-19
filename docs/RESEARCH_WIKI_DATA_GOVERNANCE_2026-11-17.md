# Research Wiki 数据治理审计与安全修复路线（2026-11-17）

## 目的与安全边界


本报告把知识图谱的“连接完整性”与“证据审查完整性”分开。审计只读数据库，不改写节点、边、证据或状态；不根据标题、摘要或模型猜测自动补边，不把检索候选升级成事实。

数据库路径：`data/research/knowledge.sqlite3`。本轮通过项目虚拟环境 Python 3.11.9 以 SQLite `mode=ro` 执行查询。所有数量均为本次快照，不代表未来运行状态。

## 1. 复核后的关键发现

- 活跃节点 910、关系边 2,459、事件 5,543；无悬空边或指向已删除节点的边。
- **156 个孤立活跃节点**是上一轮对全部节点的宽口径统计；但代码中的 `ResearchKnowledgeStore.lint()` 只把 `idea`、`experiment`、`claim` 三类孤立节点列为 `unlinked_knowledge_node`。按代码同口径复核，实际 lint 关注的孤立项为 **27 个**：`claim/proposed` 14 个、`idea/active` 13 个。source/failure 等节点不应仅因没有普通关系边就判为错误。
- **149 个没有 claim_evidence 的 claim 全部处于 proposed 状态**。这与“已审定事实缺证据”不是一回事；更合理的解释是待研究/待搜证队列，需保留为未验证主张。
- 103 个 claim 已有关联证据候选，但本次查询显示这些候选均没有对应的 `claim_evidence_reviews` 记录。候选来源不等于支持证据，也不代表已读过来源正文。
- `claim_evidence_reviews` 为 0；`source_relationships` 为 0；高级 claim 状态（supported/refuted/contested/superseded）当前为 0。
- `source_identity_candidates` 已有 5 条待审候选：canonical_equivalent 3、same_author 1、same_publisher 1。它们不是已确认的来源关系，须通过审查工作流后才可采纳。

## 2. 代码机制核验

已定位 `src/muse/research_knowledge.py`：

- `record_claim_evidence()` 只记录来源候选和检索方向，并明确把普通 `relates` 边标记为“不是支持或反驳”。
- `record_claim_evidence_review()` 追加式记录来源内容审查 verdict、质量、独立性和理由；审查记录不可更新或删除。
- `record_source_relationship()` / 来源身份候选审查工作流用于记录引用、转载、同一作者/出版方等来源谱系，避免把同源转载误认为独立证据。
- `transition_claim()` 要求证据引用与 reviewer，且已审定状态不能仅靠空引用通过。
- `lint()` 当前覆盖高级 claim 缺 reviewer/evidence、claim 间矛盾，以及 idea/experiment/claim 孤立项；它**不等同于完整证据覆盖审计**，也不会把所有 proposed claim 判为错误。

## 3. 分级治理队列

### P0：证据候选审查（先做）
对 103 个已有候选的 claim，逐条核对来源是否可访问、正文是否真正涉及主张、是否直接支持/反驳/仅提及/证据不足；记录质量、来源独立性与简明理由。必须保留反证方向，不因支持材料较多就跳过反证搜索。

### P1：孤立知识节点分类
对 14 个 proposed claim 与 13 个 active idea，先按语义和来源元数据分类：独立待办假设、研究主题入口、缺失关系、重复/近重复候选、过期或无效记录。只对有明确来源/实验/任务依据的条目提出补链建议；任何合并、删除、状态变更都需单独审查。

### P2：来源谱系审查
检查现有 5 条 source identity candidates。只有在可核验的 URL、作者、出版方、引用链等依据充分时才接受或拒绝；采纳后记录关系类型、置信度、basis 与 reviewer。不得由域名相同直接推断同一所有权或非独立。

### P3：未附证据的 proposed claims
149 个 claim 保持 proposed/unverified 语义。按优先级分批补充支持检索、反证检索与上下文材料；没有足够来源的继续保留为待研究，不制造 evidence rows 充数。

## 4. 建议实施顺序与验收标准

1. 新增只读审计命令，输出按 node_type/status 分类的孤立项、无证据 claim、未审查候选、来源候选及高级状态 claim。
2. 新增 dry-run 治理队列导出（CSV/JSON），每项包含 node_id、问题类型、当前状态、证据计数、建议动作；默认不写数据库。
3. 先对单个 claim 执行人工审查演练，验证 append-only review、审计事件、coverage 输出与 reviewer 角色隔离。
4. 先在临时测试数据库覆盖“支持、反驳、仅提及、证据不足、同源转载、独立来源、重复候选、空证据”等用例，再考虑真实库小批量写入。
5. 任何真实库写入前创建一致性备份，启用事务、记录操作者/依据、限制每批规模；写后再次只读核对节点数、边数、事件数、lint 与 coverage。失败时不得静默回滚审计轨迹。
6. 验收不是追求所有 claim 都有支持结论，而是每条进入审查的 claim 都能区分“候选、已审查、支持、反驳、提及、不足、未验证”，并保留来源独立性与反证信息。

## 5. 已落地：只读审计 CLI

新增 `src/muse/research_wiki_audit.py`，并注册命令 `muse-research-wiki-audit`。该命令直接以 SQLite URI `mode=ro` 打开现有数据库，启用 `query_only`，不实例化会执行 schema 初始化的 `ResearchKnowledgeStore`。输出机器可读 JSON，含审计摘要与治理队列；队列仅输出节点/证据/候选 ID、类型、状态和建议动作，不导出标题或正文。

项目根目录运行示例：

```powershell
.venv\Scripts\python.exe -m muse.research_wiki_audit --db data/research/knowledge.sqlite3 --limit 500```

可选 `--output reports/research-wiki-audit.json` 将结果写到指定报告文件；数据库始终只读。`--limit` 控制每类队列最多输出多少项（1–5000），避免无界输出。

## 6. 已落地：人工证据审查工作台（只读队列）

新增 `src/muse/research_wiki_review.py`，注册命令 `muse-research-wiki-review`。它只读打开生产 SQLite（`mode=ro` + `PRAGMA query_only=ON`），将尚未审查的证据候选整理为可读 JSON：主张标题/原始主张、主张状态、来源标题/链接、检索方向、证据类型、检索词、备注、质量/独立性字段与审查状态。可用 `--claim-id` 聚焦单条主张、`--include-reviewed` 查看已有审查记录、`--limit` 限制数量、`--output` 导出队列报告。

运行示例：

```powershell
.venv\Scripts\python.exe -m muse.research_wiki_review --db data/research/knowledge.sqlite3 --limit 100
```

重要边界：检索方向不是证据裁决；工作台不抓取或声称已阅读来源全文，不自动填写 reviewer/verdict，不改变 claim 状态，也不写生产数据库。人工审查写入仍应通过既有 `record_claim_evidence_review()`，并保留审查人、裁决、质量、独立性与理由。

生产库只读冒烟测试（`--limit 2`）成功，返回两条待审候选，展示了主张和来源的可追溯信息；未审查生产数据。

## 7. 已落地：显式人工证据审查写入命令

新增 `src/muse/research_wiki_review_record.py`，注册 `muse-research-wiki-review-record`。命令必须同时提供 evidence ID、reviewer ID、verdict、quality、independence、rationale，并显式带上 `--confirm-write` 才会调用既有 `record_claim_evidence_review()` 追加审查记录。

示例（以下参数必须替换为已核对的真实值；示例不会自动执行）：

```powershell
.venv\Scripts\python.exe -m muse.research_wiki_review_record --db data/research/knowledge.sqlite3 --evidence-id <EVIDENCE_ID> --reviewer-id <REVIEWER_ID> --verdict mentions --quality moderate --independence unclear --rationale "已阅读来源摘要；该材料仅提及主题，未建立主张。" --confirm-write
```

verdict 仅允许 `supports`、`refutes`、`mentions`、`insufficient`。该命令不替用户阅读来源、不生成审查理由、不自动升级 claim 状态；角色独立性仍由知识库 API 校验。写入是追加式审查记录，必须由实际审查者确认来源内容后再执行。本轮只在临时测试数据库验证写入路径，**未对生产库执行写入**。

## 8. 本轮结论

本轮在只读队列之上新增显式人工审查写入 CLI。四组联合回归（ResearchKnowledge、审计、只读队列、审查写入）共 41 项测试通过；TOML 入口校验及 compileall 通过。生产数据库只读审计统计保持不变，生产库没有执行任何证据裁决或写入。下一步优先补上来源正文快照与引用定位，使人工审查可追溯到实际阅读过的内容，再研究来源谱系和独立性门禁。

## 9. 已落地：来源快照引用锚点只读核验（2026-11-18）

新增 `src/muse/research_wiki_citation.py`，注册命令 `muse-research-wiki-citation`。它复用既有 `data/research/evidence.sqlite3`，不复制正文到知识图谱，也不重新抓取网页；以 SQLite `mode=ro` + `PRAGMA query_only=ON` 按精确 URL 读取已保存快照。

核验流程要求调用方提供 URL、预期 SHA-256 和不超过 2,000 字符的精确摘录。程序重新计算整份已存正文的 SHA-256，核对哈希后再定位摘录，输出字符起止偏移、摘录哈希、抓取时间与来源内容类型。来源快照缺失、哈希不符、摘录不存在、摘录重复、快照被截断时均拒绝生成已验证锚点；不会联网补抓，也不修改任何数据库。

命令示例：

```powershell
.venv\\Scripts\\python.exe -m muse.research_wiki_citation --db data/research/evidence.sqlite3 --source-url "https://example.org/report" --source-sha256 <SHA256> --excerpt "原文中唯一且精确的摘录"
```

当前边界：这是独立只读核验器，尚未把 citation anchor 作为结构化外键写入 `claim_evidence_reviews`；因此不能宣称审查写入已强制要求引用锚点。下一步应设计 append-only 的 review-citation 关联表及双库一致性校验，再把支持/反驳裁决门禁接入人工审查写入 CLI。

本轮新增 3 项引用核验测试；与 ResearchKnowledge、审计、只读队列、审查写入测试联合共 44 项通过。TOML 入口解析和模块 compileall 通过。仅使用临时测试数据库；生产知识库与生产证据库未写入。

## 10. 已落地：审查引用结构化绑定与裁决门禁（2026-11-18）

新增 append-only 表 `claim_evidence_review_citations`，通过 `review_id` 外键绑定审查记录，保存来源 URL、来源正文 SHA-256、精确摘录、字符偏移、摘录 SHA-256、抓取时间和内容类型。表具有禁止 UPDATE/DELETE 的触发器；审查记录与引用锚点在同一个 SQLite 事务内写入，避免出现“有裁决、无引用”或“有引用、无裁决”的半成品。

`record_claim_evidence_review()` 现在对 `supports` / `refutes` 强制要求已核验锚点，并核对锚点 URL 与该条 claim evidence 所属 source node 的 external_ref 完全一致。只读 dossier 返回每条 review 的 citations。

升级 `research_wiki_review_record.py`：支持/反驳写入必须提供 `--source-sha256` 和 `--excerpt`；CLI 先从知识库取得该证据项对应的精确来源 URL，再调用只读快照核验器验证正文哈希和唯一摘录，验证成功后才在知识库事务中写入审查与引用。跨库仅传递已核验元数据，不复制来源全文。mentions / insufficient 可不附引用；若显式提供 SHA-256 和摘录，也会一并核验保存。

示例：

```powershell
.venv\\Scripts\\python.exe -m muse.research_wiki_review_record --db data/research/knowledge.sqlite3 --evidence-db data/research/evidence.sqlite3 --evidence-id <EVIDENCE_ID> --reviewer-id <REVIEWER_ID> --verdict supports --quality high --independence independent --rationale "已核对原文方法与结果段落。" --source-sha256 <SHA256> --excerpt "原文中唯一且精确的摘录" --confirm-write
```

兼容性：新增表使用 `CREATE TABLE IF NOT EXISTS`，不改变既有表、不重写既有审查，也不提升 schema version；旧版数据库首次由正常 store 打开时仅创建新表和触发器。测试只在临时数据库执行。生产 knowledge.sqlite3 与 evidence.sqlite3 未写入。

本轮联合回归 46 项通过；其中新增端到端临时库测试验证支持裁决无引用时拒绝、正确快照引用能随审查原子保存、错误来源 URL 不得冒充目标来源。既有 44 项与本轮新增 2 项均通过。

## 11. 已落地：跨来源裁决冲突与独立证据配对摘要（2026-11-18）

在 `claim_evidence_coverage()` 中增加裁决冲突摘要：按每个来源最新的人工审查 verdict 统计支持来源与反驳来源。两类来源同时存在时，状态标记为 `contested`，并设置 `conflict_requires_human_resolution=true`；仅支持、仅反驳、尚无方向性审查分别标记为 `support_only`、`refute_only`、`no_directional_reviews`。

另增加 `explicit_independent_cross_verdict_pair_count`，只统计已在来源谱系中由人工审查明确记录 `independent_of`、且两端分别具有支持与反驳审查的来源配对。转载/衍生来源即使形成支持与反驳冲突，也不会被误报为独立交叉验证。域名不同、未发现依赖、自动身份候选均不会自动证明独立。

这只是审查摘要与人工复核提示，不自动改变 claim 状态、不做多数票、不计算可信度分数。集成测试覆盖：一条反驳来自已知转载依赖来源，另一条来自已明确独立的官方数据源；最终识别 contested，同时只把明确独立的跨裁决来源配对计为 1。claim 仍保持 proposed。

本轮五组联合回归 46 项通过。只修改代码和临时数据库测试；生产库未写入。

## 12. 已落地：来源身份候选的可解释匹配依据（2026-11-18）

复用既有 `source_identity.py` 候选生成器和 `source_identity_candidates` 审查表，没有新增重复关系模型或数据库表。每条候选的 `evidence.matched_values` 现在保存可直接复核的匹配值：规范化 canonical URL、规范化出版方名称、重合作者名列表，以及引用 / isBasedOn 命中的来源 URL。

这些字段是匹配规则的可解释输入，不是独立性、所有权、事实真实性或来源可信度证明。候选仍保持 pending；只有人工接受后才会写入正式来源关系。历史来源比较仍限制在最多 500 条，并且只比较本轮来源与历史来源，不进行历史来源之间的全量两两推断。

新增断言覆盖上述匹配值的可追溯性。六组联合回归 52 项通过，py_compile 通过；仅写入代码/测试/文档，未触碰生产知识库与证据库。

## 13. 已落地：跨研究批次的精确正文哈希匹配提示（2026-11-19）

新增只读函数 `find_exact_content_matches()`，把本轮来源与最多 500 条历史来源比较；对同一批次来源也做精确配对。只有 SHA-256 格式有效且快照明确未截断时才比较，截断正文、无效哈希和无效 URL 均跳过。匹配结果保留两个来源节点 ID、规范化 URL、哈希和 `exact_fetched_text_hash` 类型。

该结果作为 research pipeline 的 `exact_content_match_hints` 返回，不写入正式来源关系，也不声称来源独立、作者相同或事实相同。现有 source identity candidate 数据库 CHECK 类型约束未更改，因此没有把正文哈希匹配错误塞入 canonical_equivalent / derived_from 等既有关系类型。生产数据库未写入。

新增跨批次临时数据测试覆盖：有效哈希匹配、截断快照排除、无效哈希排除。七组联合回归 83 项通过，py_compile 通过。

## 14. 证据覆盖缺口驱动下一轮研究选题

- `AutonomousResearchEngine._next_topic()` 读取只读 `claim_evidence_coverage()` 摘要；对 `contested` 冲突主张生成独立证据/分歧根因复核选题，对缺少反面检索方向的主张生成反例与替代解释选题。
- 选题保留 `source_claim_id`、`source_node_ids`、缺口类型、覆盖状态、冲突状态与支持/反驳来源计数；稳定 key 绑定 claim ID，避免同一缺口重复生成不同选题。
- 搜索问题仅使用 claim 标题的有界文本，不外发审查理由、私密反馈或来源正文。候选标记 `candidate_only`，不自动改写 claim 状态、不裁决争议，也不直接执行代码改进。
- 当前只覆盖已有证据但缺少反面检索、以及已出现双向人工裁决冲突的 claim；完全无证据的 claim 仍由既有待审 backlog/研究流程处理，避免把所有空白记录膨胀为重复主题。
- 实现时发现缺口扫描原先位于知识库连接关闭之后，异常被静默跳过；现已在独立只读知识库上下文中执行扫描，并移除宽泛的静默异常吞噬。缺少反面检索的端到端候选测试通过。
- 验证：py_compile 通过；autonomous research、knowledge、Wiki audit/review/citation、source identity、research pipeline 七个测试模块共 132 项通过。需网络解析的 review-record 集成用例未纳入本轮通过数。

## 15. 哲学实验结果驱动研究跟进

- 自主研究引擎复用既有 `philosophy_experiments` 与 `philosophy_principles`，只读提取 outcome 为 `refuted` 或 `inconclusive` 的已完成实验，最多读取最近 50 条。
- 每条候选保留 `source_experiment_id`、`source_principle_key`、实验结果、指标及稳定 key；将失败或未决实验转成边界条件、测量误差、替代解释和新实验设计的研究问题。
- `planned` 与 `supported` 实验不生成此类跟进；一次实验结果只代表该实验记录，不自动证伪或证实原则，不更新实验结果和原则置信度。
- 通过 SQLite `mode=ro` 读取，不调用 `PhilosophyStore` 初始化或写入。集成测试验证实验记录前后不变。
- 验证：py_compile 通过；8 个相关测试模块 139 项通过。

## 16. 基于既有检索词对的研究去重

- 复用自主研究 idea 节点已持久化的 `research_queries`，仅比较恰好两条字符串形式的支持/反面检索词；比较前执行 `casefold()` 与空白归一化。
- 将 `research_query_pair_seen` 作为候选排序提示，不删除历史、不合并来源、不推断语义相同，也不宣称新主题具有学术新颖性。
- 选题优先级（拒绝评审跟进、正在研究的反馈提案等）仍先于重复检索提示；明确指定的 `preferred_key` 仍用于中断恢复。
- 验证：8 个相关测试模块 140 项通过，py_compile 通过。



