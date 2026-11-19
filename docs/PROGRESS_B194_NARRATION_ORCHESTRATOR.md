# B194｜Claim Dossier → Narration Run Orchestrator

日期：2026-11-19

## 实现

- 新增 persist_claim_grounded_narration_run()，把 NarrationGenerator 输出、ResearchKnowledgeStore.claim_dossier()、证据契约构建器与 NarrationSyncStore 串成一个可调用入口。
- 从逐段 claim_ids 收集唯一 Claim ID，逐一读取最多 50 条 dossier evidence，再由 B190 adapter 转换；缺失或无效 Claim 不会被推断为支持。
- 自动构建逐段 contract，并将 narration segments、timeline、audio/subtitle references 与 evidence contract 写入同一个 SQLite run。
- 无 Claim、无 dossier、候选未审核、来源时效未知均 fail-closed；不自动升级 review verdict，不自动批准视频发布。
- Claim ID 在访问数据库前严格校验，避免无效输入被传入 ResearchKnowledgeStore。

## 验证

- py_compile 五个旁白/视频模块：通过。
- 回归测试：tests.test_narration_evidence_contract、tests.test_narration_evidence_pipeline、tests.test_narration_pipeline、tests.test_video_product_builder：**89/89 passed**。
- 新增编排测试覆盖 dossier 获取、候选不升级、contract/run 同快照回读、缺失 Claim 阻断、来源时效未知阻断、不存在的 Claim 节点阻断。
- 对真实 data/research/knowledge.sqlite3 中 Claim 7fde9c3ca8bf600983068682a675ac62 执行端到端探针：读取 5 条候选，contract 正确为 blocked / no_verified_support，并成功将完全相同的 contract 写入临时 NarrationSyncStore 后回读。

## 当前端到端边界

- 现有调用方可以用该 orchestrator 生成带审计契约的旁白 run，再把同一 contract 交给 VideoProductBuilder。
- 当前还未将 orchestrator 自动接入所有 Muse 创作入口；需要逐个核实调用点并接入，避免旁路。
- VideoProductBuilder 仍是应用层 gate，后续应直接从知识库重读 dossier 并校验契约来源/签名，以减少调用方伪造 ready contract 的风险。
- 当日 Claim 的五条候选目前仍未人工审核，真实业务旁白仍会被正确阻断；测试中的 ready contract 只用于验证成功路径。
