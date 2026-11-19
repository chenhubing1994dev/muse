# B-142｜商业效果回流账本与本地 API

日期：2026-11-17
项目：Muse

## 目标

把高价值短视频的真实平台表现、线索、订单与结算数据回流到既有 Creator Initiative，作为后续选题、制作和研究优化的可追溯依据；不建立第二套账本，也不将播放量推算为收入。

## 实现

- 在既有 `data/initiatives/muse.sqlite3` 的 `InitiativeStore` 增加 append-only `initiative_performance_snapshots`，按 Creator Initiative 归档每次采集快照。
- 指标覆盖播放、点赞、评论、收藏、分享、CTA 点击、合格线索、订单、报告收入、已结算收入、制作成本，以及 3 秒留存、完播率、平均观看秒数。
- 金额使用最小货币单位整数（例如 SGD cents），并按 ISO 三字母货币码分组汇总；不同货币不混加。
- 数据来源显式区分 `official_analytics` 与 `manual_entry`；平台使用 allowlist。采集快照带幂等键，完全相同的重放返回原记录，不同内容复用同键则拒绝。
- 新增 bearer-auth loopback API：
  - `POST /api/v1/initiatives/{initiative_id}/performance` 追加快照。
  - `GET /api/v1/initiatives/{initiative_id}/performance` 读取最近 100 条及按币种汇总。
- 不自动连接或发布社交账号；没有官方授权数据时，只能由用户明确标记为手动录入。reported revenue 与 settled revenue 分开，只有后者可用于实际到账复盘。

## 验证
- `test_initiative_engine.py`：14/14 passed（含快照校验、幂等重放、货币汇总、追加式保护）。
- `test_status_api.py`：28/28 passed（含 bearer-auth POST/GET、幂等重放、无效百分比拒绝）。
- `test_pipeline_control.py`：13/13 passed（Windows 路径比较测试修正后单独复测）。
- 初次合计 55/55 项定向回归通过；本轮再次执行扩展后的 `test_initiative_engine`、`test_status_api`、`test_pipeline_control`，**61/61 passed**。
## 边界
- 本轮已接入 Muse 创作产品卡片：新增商业表现折叠面板、人工数据录入、最近快照与按币种汇总展示；通过现有 Bearer-auth API 读取/追加记录。
- 此账本不构成收入预测；播放量、预估收入和实际结算必须分开记录。
## 后续回归与当前限制

- UI 最新验证：`npm test` 45/45 passed；`npm run check` 0 errors / 0 warnings；`npm run build` 成功。
- Python 定向回归：`test_initiative_engine.py` 14/14、`test_status_api.py` 28/28、`test_pipeline_control.py` 13/13 passed。
- 旧 Python 3.14 环境曾执行 1261 项并报告 55 errors、1 failure；其中已确认部分 Provider 测试因该环境缺少项目声明的 Windows `tzdata` 依赖而无法加载 `Asia/Shanghai`，另有 Windows 路径断言已修正。此结果属于旧环境快照，不代表当前验证状态。
- Python 3.11 全仓回归：本轮一次执行 1497 tests、9 skipped、OK；该次通过在测试运行时显式设置 `NO_PROXY=*`，避免本机代理影响 loopback / DNS 相关测试。后续更完整的仓库验证见 `docs/PROGRESS.md`。
- 币种字段当前校验大写三字母格式；没有引入外部币种注册表校验。
## 2026-11-19｜B142 前端商业反馈面板接通

- 新增 `ui-prototype/src/lib/InitiativePerformancePanel.svelte`，嵌入每个 Creator Initiative 产品卡片；可展开查看最近快照、按币种汇总，并手动追加作品表现记录。
- `ui-prototype/api.js` 与 `src/lib/api.ts` 增加 GET/POST 客户端方法；POST 使用 Bearer 会话、校验 initiative ID / 平台 / 幂等键，并通过服务端 append-only 账本保存。
- 录入覆盖播放、互动、CTA、线索、订单、报告收入、已结算收入、制作成本、留存/完播与平均观看时长。货币值明确按最小单位录入；不同币种分别汇总。
- 前端只开放 `manual_entry`，没有冒充官方平台导入；尚未做 OAuth、自动采集、发布或真实到账核验。
- UI 回归 45/45、Svelte 检查 0 errors / 0 warnings、生产构建成功。
- 隔离 HTTP E2E：`test_initiative_performance_api_records_and_reads_verified_metrics` 通过；使用临时 SQLite、真实 loopback HTTP server 与 Bearer Token，POST→幂等重放→GET 回读成功，验证 2,400 播放、2 单、SGD 6,000 cents 已结算收入及 400 无效完播率拒绝。当前桌面 Muse 未监听 8765，真实生产实例录入→回读仍待验收。
