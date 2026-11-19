# B193｜VideoProductBuilder Claim-Grounded Narration Gate

日期：2026-11-19

## 实现

- package_generated_clip() 新增可选 narration_evidence_contract；传入时采用 fail-closed 规则。
- 仅接受 schema_version=1、contract=claim_grounded_narration、publishable=true、status=ready 的契约。
- 每个事实段必须有 Claim ID 且 gate_status=ready；非事实段必须 gate_status=not_applicable。
- 契约中的所有段落文字（忽略空白差异）必须与实际封装的 narration_script 完全一致，并且必须同时提供旁白音轨。
- 被阻断、无效、缺失 Claim 或脚本不匹配时，拒绝封装。
- 通过的完整契约写入 manifest.json 和 storyboard.json；ZIP 已包含 storyboard.json，因此审计记录随产品包分发。
- 契约内容纳入产品 fingerprint，避免同一视频脚本但证据状态不同却复用同一版本。
- build_from_prompt() 与粤语旁白包装流程可向下传递契约；未传契约的旧流程保持兼容。

## 验证

- py_compile 四个旁白/视频模块：通过。
- 定向回归：tests.test_narration_evidence_contract、tests.test_narration_pipeline、tests.test_video_product_builder：**85/85 passed**。
- 新增三项产品门禁测试：blocked 契约拒绝、ready 契约写入 manifest/storyboard/ZIP、契约文本与实际脚本不一致时拒绝。
- 另跑原有旁白封装测试，确认未启用契约的既有路径仍可工作。

## 重要边界

- 这是应用层契约门禁：VideoProductBuilder 验证契约结构、放行状态与脚本文本绑定，但不自行重新审核来源网页。契约必须由 Muse 的 ResearchKnowledgeStore dossier adapter 和证据契约生成器产生；若需要抵御恶意调用方伪造整个契约，后续还要把构建器与数据库审核记录做直接绑定/签名验证。
- 目前 Muse 当日 Claim 的 5 条来源仍全部 unreviewed，因此不能产出可放行的事实旁白；必须先完成人工证据审核和来源时效核验。
- 技术质检通过不等于内容事实、版权/肖像或平台政策审核通过。
