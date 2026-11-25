# Muse 商业化推进记录｜2026-11-25

## 已完成资产与验证

- 静态站预览：`outputs/site_launch_preview_20261125_v2/`，4 个页面、12 项资产；ZIP 5,454,863 bytes，SHA-256：`8958040652eed69a55ee553ce9234ed44471fbf3e90c632f2b861daab3cd14d9`。
- 预览 ZIP CRC、成员唯一性、内外 manifest、12 项资产 SHA-256 已通过；公开部署、爬取、支付均保持关闭。
- 正式 `scripts/muse-lead-server.py --static-root outputs/site_launch_preview_20261125_v2` 启动链路完成 readiness、页面与 JS GET、询价 POST 201、重复提交 200 幂等复用、SQLite 持久化验证；临时数据库已清理。
- 新增 `scripts/verify_site_preview.py`：支持显式站点目录，校验页面集合、manifest、逐项资产哈希、ZIP CRC/成员/内嵌 manifest、robots 禁爬、线索提交代码路径及 README 隐私/支付披露。默认目标为 v2，路径限制在项目 `outputs/` 下。
- 新增 `tests/test_site_preview_verifier.py`：4 项测试覆盖当前预览通过、资产篡改、公开部署门禁被打开、站点路径越界。
- 本轮官方 `muse-dev` / PSMUX 回归：新验证器 + 产品下载 + 线索接收 + 活动审核共 **24/24 PASS**；两个新文件 `py_compile` 通过。
- 再扩大回归：`tests.test_unified_runtime`、`tests.test_video_task_worker`、`tests.test_video_commander_admission`、`tests.test_site_preview_verifier` 合计 **132/132 PASS**（22.076 秒，官方 PSMUX Job `43ac6c04e062417a814556bbdb8cf20b` exit 0）。这是本轮四模块回归，不与前述 24 项相加。
- 历史 `scripts/verify_monetization_delivery_v3.py` 保持不变，仍针对旧 v3 资产；新 v2 应使用 `scripts/verify_site_preview.py`，避免把历史验证器输出误当成 v2 证据。

## 视频执行链状态

- `src/muse/unified_runtime.py` 已具备专用 `dispatch_video_pipeline_once`：在 claim 前校验冻结 brief、Commander DAG snapshot 与 digest、审批 Ticket、最新 human approval event；随后 claim、绑定 workflow plan 并调用专用 Worker。
- `VideoTaskWorker.run_approved_ticket()` 当前仍不存在。Worker 的 `run_once()` 不是该接口的替代品；统一 Runtime 的测试已覆盖缺少专用 Worker 时不 claim、拒绝 legacy Worker，以及正确路由到已批准 Ticket Worker。
- 本轮未修改由并行会话持有的 `src/muse/video_task_worker.py`。下一步继续协调唯一 writer，实现专用入口：重读持久化 execution Ticket、workflow.bound、DAG digest、审批事件、attempt、lease 与 initiative 绑定，再复用原 CommanderPlan 进入 GPU lease + persisted one-shot grant consume。不得 worker 自造计划或绕过审批。

## 商业化边界与下一步

- vidIQ 实时账号列表仍为空（本轮再次查询）；没有可发布的社交账号，没有公开发布或排程。
- 三条 55 秒 campaign v2 视频为静音内部审阅母版；完整 15 场景、文案事实、AI 披露与商业版权仍需人工审查。Research-to-Script Sprint 有三来源证据矩阵、55 秒脚本与平台文案的内部示范样稿，不是客户案例或公开报价。
- US$9 只是未验证价格假设，不是有效售价。没有真实订单或已结算收入，当前确认收入为 **US$0**。
- 下一步：先与 Worker 文件所有者协调接口并补齐端到端测试；继续制作可审阅的短视频/产品素材；账号连接后准备逐平台草稿；在具体版本审核、版权确认、收款与退款机制就绪前不开放销售或对外发布。
- 预览站仍是本机候选，不是已上线网站；robots 禁止爬取，payments disabled，public deployment false。
