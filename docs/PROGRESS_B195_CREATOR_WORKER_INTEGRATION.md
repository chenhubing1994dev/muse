# B195｜Creator Video Worker Claim-Grounded Integration

日期：2026-11-19

## 实现

- 将 claim-grounded narration 接入真实 VideoTaskWorker 创作任务入口，使用 creator_brief.video.narration.claim_grounded=true 显式启用，旧旁白任务默认不变。
- 启用时要求 claim_ids_by_segment 与旁白段数一一对应；可选 factual_by_segment 显式区分事实段与氛围段。Claim ID 校验由 NarrationGenerator 执行。
- Worker 在调用 VideoProductBuilder / Wan GPU 生成之前读取已存在的 data/research/knowledge.sqlite3，调用 B194 orchestrator 获取真实 dossier，生成证据契约并写入 data/narration/narration.sqlite3。
- 契约不允许发布时，在 GPU 生成前 fail closed，并将阻断状态和原因保留在旁白 run；知识库不存在时也 fail closed，不会静默创建空库。
- 通过的契约会随 build_options 传入 VideoProductBuilder，后续仍由 B193 的脚本匹配及产品包审计门禁执行。

## 验证

- py_compile：video_task_worker、narration_evidence_pipeline、narration_evidence_contract、narration_generator、narration_sync、video_product_builder、initiative_engine 全部通过。
- 完整定向回归：tests.test_narration_evidence_contract、tests.test_narration_evidence_pipeline、tests.test_narration_pipeline、tests.test_video_product_builder、tests.test_video_task_worker：**98/98 passed**。
- 新增真实 Worker 集成测试：建立临时 ResearchKnowledgeStore Claim（无审核证据），执行获批视频任务；旁白 run 被保存为 blocked，Worker 在 builder/GPU 调用前失败，确认 builder.calls 为空。
- 既有未启用 claim_grounded 的粤语旁白 Worker 路径仍通过测试。
- 当前快照独立复核（分模块执行）：VideoTaskWorker **11/11**、VideoProductBuilder **48/48**、Wan 视频 **14/14**、旁白证据契约+编排 **16/16**、旁白同步/时间轴 **31/31**、来源 freshness **5/5**；合计 **125/125 passed**。另对 6 个核心模块执行 py_compile，全部通过。未运行全项目 unittest，也未把分模块结果冒充全量回归。


## 边界与下一步

- 来源 freshness 已接入 B194 orchestrator：生成证据契约前重新抓取已审核引用；只有最终 URL 不变、完整内容 SHA-256 不变、原始 excerpt/offset/hash 精确匹配才标记 current。内容变化/重定向标记 stale；抓取失败或截断保持 unknown。`tests.test_source_freshness` 5/5 通过。真实知识库当前候选仍缺少人工审核的引用锚点，因此本轮没有对生产 Claim 做真实 freshness 放行，也没有启动外部抓取。
- VideoProductBuilder 已增加第二道独立门禁：重新读取 live ResearchKnowledgeStore 的 Claim dossier，重建 evidence contract，并要求与传入契约完全相等；缺库、审查/引用变化或伪造 ready contract 均 fail closed。
- 本轮未启动真实 Wan 生成，也未宣称产生新 MP4；测试只验证门禁先于 GPU 的真实 Worker 控制流。
