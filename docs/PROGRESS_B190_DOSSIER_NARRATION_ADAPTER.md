# B190｜Research Wiki Claim Dossier → Narration Evidence Contract

日期：2026-11-19

## 本轮目标

把 ResearchKnowledgeStore.claim_dossier() 的真实数据结构安全转换为旁白证据契约输入；不更改旁白生成器现有输出格式，不把检索方向或候选摘要晋升为证据结论。

## 实现

- 新增 build_evidence_by_claim_from_dossier()，只从已存储的 append-only review 读取 verdict。
- 只有审核记录内存在与来源 URL 完全匹配、且带有效 SHA-256 的引用记录，才将引用锚点标为已验证。
- 当前 dossier schema 没有独立的来源生命周期有效性审核字段，因此 source_current 一律保持 None；即使已有 supports verdict，也会因时效未知而 fail-closed。
- 检索方向 support_search_candidate / counter_search_candidate 不参与 verdict 推断。
- 不修改 NarrationGenerator、NarrationSyncStore 或 VideoProductBuilder，生产旁白链仍未接入证据门禁。

## 验证

- py_compile src/muse/narration_evidence_contract.py：通过。
- python -m unittest tests.test_narration_evidence_contract tests.test_narration_pipeline -v：**38/38 passed**。
- 对真实 knowledge.sqlite3 的 Claim 7fde9c3ca8bf600983068682a675ac62 执行 dossier → adapter → contract 探针：读取 5 条证据候选；5 条 verdict 均为 unreviewed、引用锚点均未验证、来源时效均为 unknown；事实旁白探针正确 blocked，原因为 no_verified_support。
- 首次运行使用系统 Python 时因未设置 PYTHONPATH=src 导致导入失败；修正运行环境后复测成功。这不是代码测试失败。

## 已知项目问题

- 全项目验证任务：1,525 tests passed、9 skipped，但 compileall 被 src/muse/initiative_engine.py:278-279 的缩进错误阻断。该文件已有项目改动，本轮不擅自修复。
- Runner 的 Python typed interpreter route 返回 interpreter_unavailable；改用项目现有 Python 3.14 launcher 完成上述验证，未安装依赖。
