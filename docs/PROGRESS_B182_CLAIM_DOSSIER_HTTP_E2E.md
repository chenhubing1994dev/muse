# B182｜Research Wiki Claim dossier HTTP 端到端验收

日期：2026-11-19

## 目标

为 B180 的只读 Claim dossier endpoint 增加真实 loopback HTTP 验收，而不只验证内部 builder。

## 验证内容

- 在临时 SQLite 知识库写入合成 Claim 与一条待评估证据。
- 启动本机 loopback Status API，使用 bearer token 调用 `/api/v1/knowledge/claims/{claim_id}/dossier`。
- 验证 200 响应、evidence_limit 生效、检索方向不会被包装成证据判决、confidence 不会自动评分。
- 验证不存在的 Claim 返回 404；测试只使用临时数据库与合成资料。

## 验证

- `test_status_api.py`：**34/34 passed**。
- `test_research_knowledge.py`：**37/37 passed**。
- 新增 HTTP 用例实际启动 loopback server 并完成 bearer-auth 请求；不存在 Claim 返回 404。
- 随后的 B183 全仓回归记录：Python 3.11 unittest **1498 tests，OK，9 skipped**；该全仓结果覆盖本用例，但 B183 另有并行变更，详见其独立报告。
## 安全边界

只读 HTTP GET；不触碰生产知识库，不写入 Claim 审核结论，不启动外网研究。