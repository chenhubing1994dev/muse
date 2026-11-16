# Muse 研究检索与来源读取恢复（2026-11-16）

> 本文件是 `docs/PROGRESS.md` 的补充记录。主进度文件因历史裸 CR 换行无法通过当前 revision-fenced 编辑器安全修改；本轮继续遵守 read-before-write，不绕过编辑保护。

## 本轮目标

修复“创作者灵感进入自主研究队列后，检索无结果或无法读取来源”的真实阻塞，并验证支持性与反证检索。

## 运行状态

- WebCodex Windows Runner 在线，Server / Runner 协议版本一致。
- 自主 Supervisor 状态数据库显示旧状态为 `stale`，心跳停留在 2026-11-14；本轮没有启动常驻 Supervisor，也没有把数据库中的旧 `running` 状态当成实际运行。
- 当前任务均以有界单轮诊断/研究执行；没有启动 GPU 视频生成。
- EGO Browser doctor 能找到本地可执行文件和 skill workspace，但 doctor 结果仍为 `installed_unverified`，尚不能据此宣称浏览器运行时已通过实机验证。

## 代码变更

### 1. Crossref 学术元数据备用检索

`src/muse/research.py` 当前包含：

- 无 API key 的 Crossref REST 搜索适配器，限制请求超时、结果数和响应大小。
- 主网页搜索无可用结果时，才调用 Crossref 作为备用来源。
- DOI 记录明确标记为 `crossref_metadata_review_status_unverified`；不把 Crossref 元数据等同于同行评审，更不把搜索摘要当作已读取证据。
- 量子主题仍按原规则补充 arXiv，其他主题不会无差别调用 arXiv。

### 2. 创作者研究问题检索词压缩

`src/muse/autonomous_research.py`：

- 从灵感正文中提取“研究问题”，不再把验证方法、反证条件和整段创作者叙述一起塞进检索词。
- 对“多模态创作 + 学习科学”领域使用短英文检索组合：
  - 支持方向：`interactive video lecture learning comprehension retention`
  - 反证方向：`multimedia learning cognitive load limitations`
- 其他领域保留有界的通用检索词回退。
- 创作者原始背景继续只是灵感，不升级为证据。

## 验证结果

### 自动化测试

- 最新代码下 `tests/test_autonomous_research.py`：52/52 通过。
- `tests/test_bing_rss_adapter.py` 中 Crossref 解析与备用路由测试通过；在最终检索词 profile 修改前，两个测试模块合并运行 62/62 通过。最终 profile 修改只涉及 `autonomous_research.py`，随后完整重跑该模块 52/52 通过。
- 本轮没有重新运行整个 1,230 项 Python 全量套件；不得将本轮描述为全量回归刚刚再次通过。

### 真实检索探测

Crossref 对短英文查询能够返回相关书目记录：

- 支持方向出现交互式视频讲授、学习理解、延迟记忆相关论文。
- 反证方向出现交互多媒体认知负荷、认知负荷理论、视频学习效果等记录。

这些是 Crossref 书目/摘要元数据，不代表已取得论文全文，也不构成本轮研究结论。

### 真实单轮研究

1. 创作者灵感节点 `95707746848b042a651203c57e7f026d` 首轮被选中，报告：
   `data/research/reports/autonomous-research-creator-hypothesis-review-95707746848b042a651203c57e7f026d-20261116T045349969502_0000.md`
   结果为 `no_readable_sources`，没有证据正文，未生成高价值创作提案。

2. 第二轮调度器按未研究队列选择了 `research-gap:claim-review-backlog`，而不是重复选择刚处理过的主题。报告：
   `data/research/reports/autonomous-research-research-gap-claim-review-backlog-20261116T050607931252_0000.md`
   Crossref 找到 4 个 DOI 元数据候选，但 4 个来源读取均以 `SourceReadError` 失败；系统因此没有把它们计作已读取证据，也没有生成创作提案。

3. 单独测试 `PublicSourceReader` 读取 DOI / 出版方页面也遇到 `SourceReadError`。当前瓶颈已经从“找不到书目记录”转移到“安全读取可访问全文/正文”。

## 数据保护

真实研究重试前，通过 SQLite `Connection.backup()` 对 9 个相关数据库创建快照，并对源库与备份执行 `PRAGMA integrity_check`，全部为 `ok`。

快照批次 UTC：`20261116T050438257612Z`

文件统一位于 `data/handoff/`，命名包含完整相对路径，避免多个目录中的同名 `muse.sqlite3` 相互覆盖：

- `data-research-knowledge-pre-research-retry-20261116T050438257612Z.sqlite3`
- `data-research-evidence-pre-research-retry-20261116T050438257612Z.sqlite3`
- `data-tasks-muse-pre-research-retry-20261116T050438257612Z.sqlite3`
- `data-reminders-muse-pre-research-retry-20261116T050438257612Z.sqlite3`
- `data-initiatives-muse-pre-research-retry-20261116T050438257612Z.sqlite3`
- `data-philosophy-muse-pre-research-retry-20261116T050438257612Z.sqlite3`
- `data-improvements-muse-pre-research-retry-20261116T050438257612Z.sqlite3`
- `data-autonomy-runtime-pre-research-retry-20261116T050438257612Z.sqlite3`
- `data-routines-muse-pre-research-retry-20261116T050438257612Z.sqlite3`

## 未完成与下一步

1. 验证 EGO Browser 运行时，并检查 `HybridSourceReader` 是否实际接管失败的公开页面读取；不绕过付费墙、访问控制或来源安全校验。
2. 评估 Crossref 的 `link` / 出版方 landing page / 开放获取地址字段，优先读取合法公开的 HTML/PDF；元数据和摘要必须与全文清晰区分。
3. 对无法读取的来源保留失败记录和重试状态，不得把摘要冒充全文。
4. 用显式 preferred topic key 再次执行创作者灵感主题，直到获得可校验的支持与反证正文；随后才进入 Creator Brief、质量审查和产品/视频构建。
5. 当前研究报告显示独立 reviewer 未配置（`MUSE_REVIEWER_MODEL_not_configured`）；在独立审查能力通过验证前，不得把改进或创作提案标记为已批准。
6. 常驻 Supervisor 仍需单独恢复与实机验收；本轮没有宣称后台自主循环已恢复。

## 验收边界

本轮完成的是：Crossref 备用书目检索、创作者检索词压缩、相关自动化测试，以及真实检索故障定位。

本轮尚未完成的是：可靠的公开全文读取、该条灵感的完整证据研究、Creator Brief 的真实生成、独立审查通过、真实 MP4 产出和常驻自主运行。
