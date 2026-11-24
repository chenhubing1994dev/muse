# B389 — 自主运行状态语义与安全恢复

日期：2026-11-24

## 修复

- RuntimeStateStore.status() 不再把“正常停止、无 owner、无 PID”的旧 heartbeat 误报为 stale。
- 新增 heartbeat_status：active runtime 为 fresh/stale；正常 stopped runtime 为 inactive。
- 如果 state 非 active 但仍残留 owner_id 或 PID，则仍标记 stale，避免隐藏孤儿进程/状态损坏。
- Web Portal 优先识别 state=stopped 且无 owner/PID，显示 “STOPPED — Start to resume routines”，不再显示误导性的 “HEARTBEAT STALE”。
- Web Portal 原有 Start/Pause/Resume/Stop 控件与 /api/v1/portal/runtime/control 路由已存在；本轮没有重复造控制入口。

## 真实运行状态只读审计

- 正式 runtime DB 当前 state=stopped、owner_id=null、pid=null；旧 heartbeat 时间是 2026-11-16。
- daemon control log 在 2026-11-16 记录了两次 start/stop；runtime.stdout.log 显示各次干净退出（2 ticks、0 tasks_processed、0 errors），runtime.stderr.log 为空。证据更符合明确 stop，而非崩溃。
- 6 条 routines 仍 enabled；idle-autonomous-research 的旧 running lease 已过期，RoutineStore.claim_due() 可在下次启动时回收。
- TaskQueue 当前 9 succeeded、38 failed、0 queued、0 running、0 waiting_approval。失败项是历史记录，主要集中在旧研究/模型评审输出格式错误，另有一条旧视频验收任务 handler.unexpected；没有自动重试，也没有清理。
- 因控制日志存在明确 stop，本轮没有擅自启动常驻 daemon，避免覆盖此前的停止意图。Start 控件仍可由用户主动触发。

## 回归

- tests.test_autonomous_runtime：31/31 通过，覆盖 stopped/inactive 与 active/stale 两种状态。
- Autonomous Runtime + Autonomous Research + Status API + Web Portal 集成回归：168/168 通过。
- Web Portal 单独回归：19/19 通过。
- Python py_compile 与 web-portal/app.js Node 语法检查通过。

## 尚未完成

1. 是否重新启动常驻自主运行 daemon，需要用户明确决定；启动后会恢复到期 routines，可能产生新的研究任务。
2. 38 条历史 failed tasks 仍需只读分类与逐项决定是否重试；不得批量重放。
3. Web Server Edition 多用户/管理员身份与公网部署安全、插件执行沙箱、Android/iOS/macOS 交付仍未完成。
4. 产品原型人工反馈回写、验收、版本晋级与回滚仍待打通。

正式数据库只读审计；历史测试污染及不可擅自清理事项详见 docs/PROGRESS_B388_TASK_QUEUE_LEASE_CLOCK.md。
