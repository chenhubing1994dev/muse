# B192｜NarrationSyncStore Evidence Contract Persistence

日期：2026-11-19

## 实现

- NarrationSyncStore 新增可选 evidence_contract 参数，将 claim-grounded narration contract 与旁白文本、音频路径和时间轴作为同一 run 快照持久化。
- 新增 SQLite evidence_contract_json 列；对旧 narration_runs 表执行幂等式列检查与 ALTER TABLE 迁移，不删除或重建旧数据。
- get_run() 与 list_runs() 回读完整 evidence_contract；旧记录统一返回 null，不伪造历史审核状态。
- 写入前验证契约 schema_version、contract、publishable/status 一致性及 segments 类型；JSON 禁止 NaN，单字段大小限制 1 MB。
- 不改变未传 evidence_contract 的旧调用路径。

## 验证

- py_compile narration_generator.py、narration_evidence_contract.py、narration_sync.py：通过。
- python -m unittest tests.test_narration_evidence_contract tests.test_narration_pipeline -v：**43/43 passed**。
- 新增证据契约写入/回读、无效 schema 拒绝、旧数据库列迁移三项测试。

## 边界

- 这是旁白 run 的审计快照持久化，不等于产品发布门禁。
- VideoProductBuilder 尚未检查 evidence_contract；下一步 B193 将为显式 claim-grounded 视频构建模式加入 fail-closed 产品门禁和产品包审计清单。
