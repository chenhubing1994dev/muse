# B189｜Claim-Grounded Narration 第一阶段

日期：2026-11-19

## 研究入库

- 目标 Claim：`7fde9c3ca8bf600983068682a675ac62`，标题为「让每句旁白都有证据身份证」。
- 通过 `ResearchKnowledgeStore` 幂等写入 5 个 source 节点及 5 条 `claim_evidence` 关系：3 条 `support_search_candidate`、2 条 `counter_search_candidate`。
- 候选包括 CiteGuard、Lifecycle Faithfulness、Groundedness Is Not Truth、Cited but Unused、Correctness is not Faithfulness。
- 回读确认 5 条关联均为 `quality=unassessed`、`independence=unassessed`，没有添加任何 `claim_evidence_reviews`；原 Claim 仍为 `proposed`，`review_state=not_researched`。
- 这些条目目前是摘要级研究候选，不是已审核证据，也不是支持/反驳结论。
- 写入前使用 `sqlite3.Connection.backup()` 创建唯一的 pre-write 快照，`PRAGMA integrity_check=ok`；没有覆盖当天已有快照。

## 实现

- 新增 `src/muse/narration_evidence_contract.py`，提供纯函数 `build_narration_evidence_contract()`。
- 对事实性旁白句要求 Claim ID；只有同时具备已审核 `supports`、已验证引用锚点、明确当前有效的来源才可通过。
- 已验证的 `refutes` 反证会阻断；过期来源、未知时效、未验证锚点、未审核候选和缺少 Claim 均 fail-closed。
- 非事实性氛围/转场句可显式标记 `factual=false`，不强制要求 Claim。
- 输出包含句子 SHA-256、Claim/Evidence ID、可选时间码、阻断原因和整体 publishable 状态。
- 这是独立的第一阶段契约模块，尚未接入 `NarrationGenerator`、`NarrationSyncStore` 或 `VideoProductBuilder`；不会误称生产视频链路已受门禁保护。

## 验证

- `python -m unittest tests.test_narration_evidence_contract tests.test_narration_pipeline -v`：**35/35 passed**（新契约 9 项 + 现有旁白/TTS/时间轴 26 项）。
- `python -m compileall -q src/muse/narration_evidence_contract.py`：通过。
- 项目虚拟环境没有安装 pytest；没有安装新依赖，使用标准库 unittest。
- 下一步将契约以可选 manifest 接入旁白与时间码产物，再增加 VideoProductBuilder 产品构建门禁测试；本轮尚未接入生产调用链。

## 边界

- 不自动创建证据审核 verdict，不自动晋升 Claim，不自动批准代码升级。
- 未观察到此 Claim 对应的后台研究 Job 已启动或完成。
- 本轮没有改动 `NarrationGenerator` 的既有输出契约，也没有声称视频生产链已完成事实核验。