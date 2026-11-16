# Muse 开发进度补充：学术检索回退与真实研究循环

日期：2026-11-16

## 本轮目标

修复自主研究在主搜索服务无结果或不可用时缺少学术元数据回退的问题；用真实 Runner 单轮运行验证队列行为；不把元数据、摘要或创造者灵感误记为事实证据。

## 已完成的代码变更

### Crossref REST 学术元数据回退

修改：`src/muse/research.py`

- 新增 `CrossrefSearchAdapter`，调用公开 Crossref Works REST API。
- 对 query、limit、超时、响应体大小、JSON 结构和 DOI 格式做边界校验。
- 仅接受带有效 DOI 与标题的记录，按 DOI 去重。
- 解析发表年份、出版类型、出版方与摘要；摘要移除 JATS HTML 标签并解码实体。
- 每条记录标记 `source_kind=crossref_metadata_review_status_unverified`，明确表示仅为 Crossref 元数据，同行评审状态仍未核实。
- 在 `QuantumAwareSearchAdapter` 中，当主搜索无结果时调用 Crossref；保留原有 arXiv 量子专题逻辑。
- 不把 Crossref 元数据直接提升为支持/反证，也不绕过来源正文读取、SHA-256 快照与主张审查。

## 测试结果

在 Runner 上使用 Python 3.14 与项目测试依赖运行：

- `test_bing_rss_adapter.py`：10/10 通过。
- `test_autonomous_research.py`：52/52 通过。
- `test_research.py`：6/6 通过。

其中一次初测发现日期字段期望应为年份，已修正后重新运行，10/10 全绿。

## 真实运行观察

- 使用 Runner 执行一次 `muse.autonomous_runtime research-once`，Job `wc_job_Li9u3c3xwXtssCeE` 已终态完成，退出码 0。
- 本次选题器实际选中 `research-gap:claim-review-backlog`，而非本轮新增的创造者灵感；生成报告：
  `data/research/reports/autonomous-research-research-gap-claim-review-backlog-20261116T050537673890_0000.md`
- 本次报告读取到 1 个来源、1 个域名；独立审查模型未配置（`MUSE_REVIEWER_MODEL_not_configured`），因此不能称为已通过独立审查。
- 这次运行证明单轮研究任务可在 Runner 上执行并正常退出，但不能证明 Supervisor 常驻运行，也不能证明创造者灵感已完成研究或生成作品。

## 真实数据保护

在上述研究运行前，使用 SQLite Backup API 对下列数据库创建快照，所有副本 `PRAGMA integrity_check=ok`：

- `data/handoff/research-knowledge-pre-crossref-20261116T050507798159Z.sqlite3`
- `data/handoff/research-evidence-pre-crossref-20261116T050507798159Z.sqlite3`
- `data/handoff/tasks-muse-pre-crossref-20261116T050507798159Z.sqlite3`
- `data/handoff/reminders-muse-pre-crossref-20261116T050507798159Z.sqlite3`
- `data/handoff/improvements-muse-pre-crossref-20261116T050507798159Z.sqlite3`
- `data/handoff/initiatives-muse-pre-crossref-20261116T050507798159Z.sqlite3`

## 仍未完成 / 下一步

1. 在 Runner 网络条件稳定时，对 Crossref 回退执行真实在线检索回归；本轮一次直接调用遇到 TLS 握手错误，说明网络可用性仍需持续验证。
2. DOI 落地页的正文读取有时会得到过短内容；需要增加开放获取全文发现（例如 OpenAlex/Unpaywall 等合法公开元数据），并且只有成功读取并哈希的正文才进入证据层。
3. 检查选题队列公平性，避免高优先级 claim-review backlog 长期压住 creator-inspired hypothesis。
4. 为高质量研究配置独立审查器；当前 reviewer 未配置时必须保持待审状态。
5. 只有在证据覆盖、反证检索、正文快照和独立审查门槛满足后，才生成/推进 Creator Brief 与 VideoProductBuilder 任务。
6. 实测 Supervisor 的过期租约接管、常驻心跳、暂停/恢复与崩溃恢复；不能只根据数据库历史状态推断正在运行。

## 认识论与安全边界

- Crossref 记录是学术元数据，不自动等于同行评审，也不等于研究结论。
- 搜索摘要不是已读取原文；来源数量与域名数量不构成可信度评分。
- 易经、紫微、奇门、大六壬等象征体系只作问题启发，不作证据。
- 不自动批准代码升级，不把未实际生成的 MP4、工程包或创作任务写成已完成。
