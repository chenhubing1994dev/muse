# 2026-11-16｜灵感收件箱幂等与自主研究入队打通

## 本轮交付

- 复用既有本地 Dashboard API `POST /api/v1/inspirations` 与 Research Wiki，不另建重复的灵感库或调度器。
- 灵感入口新增可选 `title`、`tags`、`research_domains`、`source`；来源枚举包含 `chatgpt_daily_inspiration`、`muse_local_dashboard`、`user_manual`。GET 收件箱同步返回标签、研究领域与来源。
- 将灵感内容 SHA-256 纳入稳定 `external_ref`，相同内容重复提交会 upsert 到同一知识节点，避免请求重试生成重复灵感；保存内容哈希、认识状态、标签、研究领域与来源溯源。
- 保留 `creator_inspired_hypothesis_only` 与 `review_state=not_researched`；只有 `allow_external_research=true` 的灵感才会被 `AutonomousResearchEngine._next_topic` 纳入研究候选，并生成独立 `support_query` / `counter_query`。玄学/象征灵感继续只作假设，不参与事实证据。
- 修复来源字段类型校验：非字符串来源返回 400，不再因不可哈希输入导致 HTTP handler 异常断连。

## TDD 与验证

- 新增收件箱扩展字段、幂等、研究队列集成测试；先观察到旧入口因额外字段返回 400，再实现后通过。
- 为恶意类型输入先观察到 TypeError/连接断开，再增加字符串类型校验，最终返回 400 并通过测试。
- 收件箱端到端专项：1/1 通过。
- `tests.test_status_api` 与 `tests.test_autonomous_research`：合计 77 项通过。
- 完整 `python -m unittest discover -s tests -v`：在设置 `PYTHONPATH=src;.muse-test-deps` 后 1230/1230 通过（129.363 秒）。首次使用全局解释器未加载项目隔离 tzdata，产生 53 项环境性错误；未改动全局环境，切换项目隔离依赖路径后全绿。

## 数据保护

- 对 `data/research/knowledge.sqlite3` 使用 Python `sqlite3.Connection.backup()` 建立 `data/handoff/knowledge-before-inspiration-inbox-20261116.sqlite3`。
- 备份 `PRAGMA integrity_check=ok`，快照包含 846 nodes、2410 edges、5027 events。
- 本轮测试均使用临时数据库，未向真实知识库写入虚构灵感。

## 尚未完成与边界

- API 与研究队列的本地链路已由集成测试验证；ChatGPT 对话内容仍不会自动穿透到用户电脑。
- 每日灵感自动发送、从 ChatGPT 到本地 API 的安全桥接、实际本机 Dashboard 写入验收、Muse 独立创作提案及视频自动产出仍需后续接通与实机验收。
- `docs/PROGRESS.md` 因历史文件含不受支持的裸 CR 换行，结构化编辑器拒绝修改；本轮未用脚本绕过版本保护，改以本补充进度文件保留本轮记录。

## Dashboard 灵感工坊补强

- 本地 UI 新增分类标签与研究领域输入框，支持中英文逗号分隔，最多提交 12 个标签/领域。
- 保存时通过原有受认证本地 API 提交 `tags`、`research_domains` 与 `source=muse_local_dashboard`；成功后清空输入并刷新历史。
- 灵感历史显示标签与研究领域，便于长期浏览和分类。
- TypeScript API 类型同步支持 `title`、`tags`、`research_domains` 与来源枚举；API 客户端测试覆盖扩展 payload。
- 前端验证：`npm test` 29/29 通过；`npm run check` 0 errors / 0 warnings；`npm run build` 成功（Vite 117 modules）。`ui-prototype/dist` 已重新生成。

## 当日灵感样本已写入真实知识库

- 标题：**让反证成为 Muse 的创作引擎**。
- 已通过 `ResearchKnowledgeStore.upsert_node()` 写入真实 `data/research/knowledge.sqlite3`，并用 `get_node()` 回读校验成功。
- 节点 ID：`95707746848b042a651203c57e7f026d`；内容 SHA-256：`196a0e20a95f9059d445ed5a44d677af121970830cc3f92babff3a7fd25dc7ca`。
- 状态：`not_researched`；`allow_external_research=true`；来源 `chatgpt_daily_inspiration`；标签：意识体、研究驱动创作、反证优先、闭环进化；领域：自主研究、知识图谱、多模态创作、学习科学。
- 已明确写入 `creator_context_is_evidence=false`、`requires_support_and_counter_evidence=true`；内容包含研究问题、实验对照和明确反证条件。
- 该条已符合自主研究引擎 creator-inspired backlog 的候选筛选条件；本轮只完成入库和回读，不声称后台已经开始检索或已产生研究结论。

## 每日自动灵感任务

- 已创建 ChatGPT 自动化：`Automation_008604b65d648191904b4a996dba271b`。
- 从 2026-11-17 起每日约 12:00（Asia/Shanghai，弹性投递）生成一条原创可证伪灵感，并尝试通过继承的 WebCodex/Runner 环境写入 Muse；写入后必须回读验证。
- 若自动化运行时无法访问本地 Runner，则只交付灵感与可导入 JSON，并明确标记未写入；不得将生成内容冒充为已入库或已研究。

