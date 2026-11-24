# B388 — TaskQueue 租约时钟与锁等待竞态修复

日期：2026-11-24

## 问题

SQLite writer lock 等待可能使调用方在等待前计算 lease timestamp。若等待期间原 lease 到期，旧时间戳可能让旧 Worker 在锁释放后错误续租，或让 claim_next 创建已缩短/已过期的 lease。

## 修复

- src/muse/task_queue.py 新增统一的 _now_datetime() 时钟入口。
- claim_next() 在 BEGIN IMMEDIATE 成功取得 writer lock 后，才采样 now、timestamp 并计算 expires。
- recover_expired() 在 BEGIN IMMEDIATE 后采样时间，避免锁等待期间过期的 lease 被本轮 recovery 漏掉。
- heartbeat() 保持 SAVEPOINT 原子边界：先执行 no-op UPDATE 获取 writer lock，再采样时间并进行 lease_expires_at 条件更新；成功时同一事务写 assignment.lease_heartbeat 审计事件，失效/过期/错误 owner 不写续租事件。
- 没有删除旧事件或修改正式数据库。

## 新增回归

- test_claim_next_samples_lease_clock_after_writer_lock：证明 claim_next 不会在获取 writer lock 前采样租约时钟。
- test_recover_expired_samples_clock_after_writer_lock：证明 recovery 的时钟采样发生在 BEGIN IMMEDIATE 之后。
- test_heartbeat_waiting_for_writer_lock_cannot_revive_expired_lease：锁等待期间 lease 到期后，heartbeat 必须返回 None，且不得写 assignment.lease_heartbeat 事件。

TDD 记录：claim_next 新测试在修复前稳定复现 RED；修复后全部通过。

## 验证

全部测试均在隔离 Linux 原生副本 $HOME/work/Muse-linux-native 执行；没有从正式项目根目录启动测试。

| 检查 | 结果 |
|---|---:|
| TaskQueue + Watchdog + PluginPlatform | 106/106 通过 |
| Autonomous Research + Product Builder + Inspiration Productization/Evolution + Status API | 150/150 通过 |
| Web Portal 产品库与任务自动刷新 | 18/18 通过 |
| Python py_compile（变更文件） | 通过 |
| Node app.js / sw.js 语法检查 | 通过 |
| Ruff | 当前隔离环境未安装，未声称通过 |

此前另一个隔离目录 .muse-test-sandbox 的全量基线为 2,475/2,475、0 failures/0 errors；该结果早于 B387/B388，**本轮没有声称重新跑过 2,475 项全量测试**。

## 历史数据完整性警示

此前一次并行全量回归从正式项目根目录运行，10 个 AutonomousResearch run_once 测试缺少 workspace 参数，造成正式 DB 测试写入：22 条 Initiative knowledge links、66 条 experiences、204 条 philosophy links、2 条 pending proposals、42 条 website events。记录未删除、未回滚。

这 10 个测试当前已显式绑定 workspace=root；本轮 Research/Product pipeline 150/150 在隔离副本通过。本轮没有触碰正式 data/。正式数据库后续只做只读取证；任何清理必须另行明确批准并先制定备份/可逆方案。

## 尚未关闭的工程事项

1. Web Server Edition 生产化：目前仍需完善多用户/管理员身份、强随机初始凭证、公开 URL 与可信代理部署检查；当前 configured_public_url 为空，不应对公网开放。
2. 插件执行沙箱：动态插件执行仍默认关闭；需要签名信任、权限/资源配额、WASM 或受限子进程沙箱、Commander 审批与回滚。
3. Android SDK/NDK APK 构建、iOS/macOS CI、公网部署与真实设备 PWA 验收仍未完成。
4. 产品原型内人工反馈回写 Initiative / Research Wiki，以及人工验收、版本晋级和回滚流程仍待实现。
