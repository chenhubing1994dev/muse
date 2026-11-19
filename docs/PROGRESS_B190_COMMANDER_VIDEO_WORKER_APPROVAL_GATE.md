# B190｜Commander 审批绑定的视频 Worker 隔离

日期：2026-11-19

## 背景

此前视频 Worker 从 `initiative_video_attempts` 的通用 queued 队列领取最早任务。生产库里存在大量历史 `research-*` 自主研究候选；这些记录没有商业视频审批票据。直接启动 Worker 可能误领候选并调用 GPU。

## 本轮变更

- `initiative_video_attempts` 增加可空 `approval_task_id`，旧记录保留 NULL，兼容既有研究任务；普通 `start_video_attempt()` 不会自动获得审批身份。
- `InitiativeStore.list_queued_approval_video_attempts()` 只返回带审批绑定的 queued attempt。
- `claim_next_video_attempt(..., attempt_id=...)` 支持精确领取，并要求该记录存在审批绑定；通用旧队列行为保留给原调用者。
- 商业机会桥接创建 attempt 时持久化 Commander Ticket ID；幂等重放还会核对该 Ticket ID。
- `VideoTaskWorker` 不再从通用队列领取。它先核验 Commander Ticket 仍处于 queued、类型与 scope 正确、initiative/attempt ID 一致、`_approved` 为真、持久化 `human.decision=approved` 事件与批准者一致、当前视频 Brief 与 Ticket Brief 的 SHA-256 一致。
- 在真正调用 Builder 前再做一次审批核验，降低排队后撤销/变更的竞态风险。任何缺失或异常均失败关闭，不启动 GPU。
- 生产 Worker 一次性安全探测返回 `idle / approval_required`；未领取任务。

## 验证

- `tests.test_video_task_worker`: 8/8 passed。
- `tests.test_business_opportunities`: 14/14 passed。
- `tests.test_initiative_engine`: 17/17 passed。
- 变更涉及的 6 个 Python 源码/测试文件 `py_compile` 通过。
- 生产 SQLite 只读检查：视频队列 1,001 条均为 queued，approval_task_id 绑定数为 0；Commander 任务库没有商业视频审批 Ticket。Worker 实际 run_once 安全返回 idle。
- ComfyUI loopback 端点已恢复并能读取设备遥测，但没有获批的商业视频任务，因此本轮没有启动 Wan 推理，也没有生成 MP4/ZIP。

## 全仓验证状态

- 首次 `compileall` 与并行文件写入时间重叠，观察到的语法错误在并行改动落盘后已消失；随后重新执行 `compileall -q src/muse tests`，全仓 Python 源码与测试文件编译通过。
- 对自主研究模块补跑 `tests.test_autonomous_research`：**56/56 passed**。
- 全仓完整 unittest 尚未成功启动/完成，因此本轮不宣称全仓测试全绿；专项审批 Worker 三组回归及自主研究 56 项回归有独立结果。
- 工作区存在大量并行未提交改动，workspace review 快照因并发变更过期；未提交、未重置、未覆盖其他任务的文件。
## 备份说明

`reports/backups/muse-initiatives-before-commander-approval-binding-20261119.sqlite3` 是本轮测试后生成的 SQLite 一致性快照（integrity_check=ok），**不是迁移前备份**；名称有历史误导性，不应作为迁移前回滚点。当前生产数据库本身 integrity_check=ok。
