# Muse 自主商业机会引擎

## 目标
市场机会研究闭环：候选机会 → 证据与反证 → 价值评分 → 小规模验证 → 创作/服务试产 → 真实表现复盘 → 改进任务。

## 第一阶段
- BusinessOpportunityStore 与 muse-business CLI；SQLite 保存机会、证据和生命周期事件。
- 评分启发式：需求信号 25%、付费意愿 25%、交付适配 20%、竞争差异 15%、可重复性 15%，按风险扣分；不是成功概率、市场事实或收入预测。
- 价格/成本以 minor currency units 保存，属于估算输入，不代表实际利润。
- 证据标记 observed / reported / assumption；公开来源必须保留 HTTP(S) URL；证据不足标记 needs_more_evidence。
- shortlist 默认最多三项；幂等键防止重复写入；证据与事件 append-only。
- `promote` 通过现有 `InitiativeStore.propose()` 建立可追溯 Creator Initiative 草案，保存结构化 business_plan 与 Creator Brief / `creator_brief.video`；商业试点 Brief 同时声明 zh-HK 旁白脚本策略（`yujie_canto`）与本地 TTS 要求；草案保持 `proposed` + `draft`，不触发生成或执行。
- `request-video-approval` 创建 Commander `waiting_approval` Ticket，包含确定性 attempt ID 与视频 Brief 摘要哈希；此时不会建立视频 attempt，也不会消耗 GPU。
- `queue-approved-video` 只接受同一机会/initiative 的 Ticket，验证 `TaskQueue` 中持久化的 `human.decision=approved`、`_approved` 与 Brief 哈希一致后，才建立幂等 `prompt_to_video` attempt。它只排队、不启动 GPU；现有 Worker 仍受 `MUSE_ENABLE_VIDEO_WORKER=1` 和 Runtime pause/stop 控制。Python 3.11 主 Worker 在获批后生成粤语脚本，并在渲染前预检独立 Python 3.14 TTS 子进程；Wan MP4 随后交给本地 TTS/产品封装链。Python 3.14、TTS 包或模型缺失时在调用视频 Builder 前失败关闭，不下载模型、不启动服务。ASR 目前仍要求主运行时为 Python 3.14。完成后仍须人工审片。取消/非 queued 的 Ticket 不可派生视频任务。
- 真实播放、线索、订单、实收收入和制作成本继续复用现有 `InitiativeStore.record_performance()`，避免重复账本。

## 安全边界
只写本地 SQLite，并复用本机 Creator Initiative 数据库；不自动联网搜寻、不联系客户、不发送消息、不发布、不投广告、不付款、不创建外部账户。evidence_supported_candidate 只代表有多类非假设信号，不等于商业验证成功。机会提升只创建草案；视频必须经 Commander 持久化人工批准才可进入现有 Worker 队列。队列建立本身不启动 GPU，Worker 默认关闭且不自动发布；后续内容审片、版权/披露检查与发布仍须分别通过现有门禁。

## 使用
```bash
muse-business add --idempotency-key pilot-001 --title "粤语短片服务" \
  --customer-segment "本地餐饮商户" --customer-problem "缺少持续短视频内容" \
  --proposed-offer "每周五条粤语短片" --validation-test "向三家商户展示样片" \
  --success-signal "至少一家愿意预约付费试点" \
  --demand-signal 4 --willingness-to-pay 3 --delivery-fit 5 \
  --competition-gap 3 --repeatability 4 --risk 2
muse-business shortlist
muse-business promote <opportunity_id> --initiative-db data/initiatives/muse.sqlite3
muse-business request-video-approval <opportunity_id> --initiative-db data/initiatives/muse.sqlite3 --task-db data/tasks/muse.sqlite3
muse-business queue-approved-video <opportunity_id> <approval_task_id> --initiative-db data/initiatives/muse.sqlite3 --task-db data/tasks/muse.sqlite3
```

默认数据库：data/business/opportunities.sqlite3。使用 --db 可指定隔离测试数据库。

## 验证范围
- 本轮专项回归：商业机会/Commander **14/14**、VideoTaskWorker **7/7**、VideoProductBuilder **39/39**、旁白生成与同步 **26/26**、Python 双运行时桥接 **4/4**，合计 **90/90 passed**；全仓 Python 3.11 unittest **1518 项通过、9 项跳过**。另以 Python 3.11 主环境真实调用 Python 3.14 TTS 子进程，生成 `reports/cantonese-tts-bridge-smoke-20261119.wav`（10.96 秒、48 kHz 双声道、2.10 MB）。本轮未启动 ComfyUI/Wan，未产出新 MP4/ZIP，尚未完成人工听审；`yujie_canto` 只控制脚本措辞，TTS 声线仍为中性默认声线。

## B190 Worker 二次审批门禁补充
- Worker 只领取 `approval_task_id` 非空的精确 attempt；旧自主研究队列不会被商业视频 Worker 消费。
- 领取前与调用 Builder 前均重新核验 Commander Ticket 持久化批准事件、当前 Brief 哈希与 attempt/initiative 绑定；Ticket 取消、状态改变或 Brief 修改均失败关闭。
- 本轮 Worker 专项测试更新为 **8/8 passed**；生产队列实查没有任何审批绑定任务，因此 Worker 安全返回 idle，没有执行 Wan 推理。全仓 compileall 受并行文件语法问题阻塞，见 `docs/PROGRESS_B190_COMMANDER_VIDEO_WORKER_APPROVAL_GATE.md`。
