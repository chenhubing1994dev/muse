# B180 Research Wiki Claim Dossier Read-only API

日期：2026-11-19

## 目标

将已有 ResearchKnowledgeStore 的 Claim dossier 能力安全暴露给本地 Dashboard API，避免另建平行证据图谱。

## 实现

- 新增 `build_claim_dossier()`：校验 32 位小写十六进制 Claim ID，限制 `evidence_limit` 为 1–100；只委托现有 `ResearchKnowledgeStore.claim_dossier()`，不存在或非活动 Claim 返回空结果。
- 新增只读 GET 路由 `/api/v1/knowledge/claims/{claim_id}/dossier`。未配置知识库返回 503，不存在 Claim 返回 404，成功返回版本化 dossier。
- 沿用既有 dossier 的证据方向语义：`support_search_candidate` / `counter_search_candidate` 仅表示检索目的，不等同于证据判决；保留证据质量、独立性、审核状态和置信度边界，不自动评分、不自动改变主张。
- 未新增数据库表或迁移；不写入知识库、不访问外网、不触发研究任务。

## 验证

- `tests.test_status_api.ClaimDossierApiTests` + `tests.test_research_knowledge`：39 tests，OK。
- 完整 `tests.test_status_api`：33 tests，OK。
- Python 3.11 `py_compile`（status_api.py、test_status_api.py）在定向运行脚本中执行，退出码 0。

## 边界与后续

- 本轮测试验证 builder 和现有 Status API 回归；尚未新增针对 dossier HTTP 路由本身的端到端请求测试。
- 前端尚未展示 Claim dossier；下一步在现有 Research Wiki UI 增加支持/反向检索证据分栏，明确区分“待审”“已审核支持”“已审核反驳”和“证据不足”。
- 不宣称全仓回归通过；本轮仅完成上述定向测试。
