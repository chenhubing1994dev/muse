# B185 Research Wiki Claim Evidence UI

日期：2026-11-19

## 实现

- Research Wiki 星图点击 Claim 节点时，继续读取既有 node detail，并调用 B180 只读 dossier API。
- 在节点检查器展示原始主张、认识状态、证据覆盖、人工审核覆盖、冲突状态及置信度依据。
- 将 `support_search_candidate` 与 `counter_search_candidate` 分栏展示，来源链接仅对 HTTP(S) 地址提供外链；显示最新审核 verdict/reviewer 或“待人工审核”。
- 明确提示检索方向不是证据判决，自动评分关闭；不增加写入、审核或晋升按钮。
- API 客户端新增 `knowledgeClaimDossier()`，校验 Claim ID 与 1–100 证据上限，使用现有 bearer-auth、no-store GET。
- 为 Claim dossier 客户端新增验证测试，非法 ID/条数在请求前拒绝。

## 验证

- UI Node 测试：**45/45 passed**。
- `npm run check`：**0 errors / 0 warnings**。
- `npm run build`：Vite 7.3.7，132 modules transformed，build 成功。

## 边界

- B182 正在由并行任务补充后端 loopback HTTP 端到端验收；本任务不覆盖其文件。
- 本轮没有真实生产知识库浏览器验收；前端 build 成功不等同于已在用户桌面服务运行。
- 未更改证据、Claim、审核记录；未触碰生产知识库；没有自动判断事实或自动晋升。
