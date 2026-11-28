# 01 · 融合拓扑与缝合断层地图（b391-full-fusion）

> 扫描时间 2026-11-27 20:50 CST。范围：`src/muse`（255 模块 → 本轮 +8 = 263）、`scripts`（105 py）、`tests`（281 py）、`web-portal`、`ui-prototype`、`src-tauri`。黑名单目录未读取。

## 1. 控制平面与唯一事实源
```
入口层   muse-chat / chatgpt_bridge / task_cli / intake_runtime / status_api(/api/v1/*) / local_dashboard / web_portal / Tauri sidecar
           │
统帅层   muse_commander.MuseCommander  ← 本轮新增的唯一门面（组合下列原语，不新建队列）
           ├─ task_queue.TaskQueue          SSOT：票据/DAG 依赖/审批/租约/fencing/事件
           ├─ autonomy_safety.SafetyController  暂停/Kill Switch/熔断/背压
           ├─ consciousness_loop.assess_risk    风险分级
           ├─ review_gate.IndependentReviewGate 独立审查（builder≠reviewer）
           ├─ usage_ledger.UsageLedger          成本账本
           ├─ resource_governor                 并发上限（GPU 未探测=unknown）
           └─ adapter_hub.AdapterHub            外部框架唯一入口（Commander 授权）
并行既有  unified_runtime.MuseRuntime.dispatch_commander_once（自治运行时、视频线）← 待收口（P1-02）
```

## 2. 三条主干调用链（目标形态 → 当前接线）
| 主干 | 链路 | 本轮接线 |
|---|---|---|
| A 求真与创造 | inspiration/xuanshu_fusion → metaphysics_label → **research_wiki**(7 对象) → **dual_track_memory** → obsidian_vault → product_builder/publish_gate → feedback → evolution_guard | 诊断 L5.A 全链确定性贯通；生产入口（status_api）尚未调用 research_wiki 门面（P1-04） |
| B 统帅调度 | 目标 → **MuseCommander.submit_goal** → 风险门 → 人工审批 → run_next(超时/取消) → 客观审查 → 凭证链 + 成本 | 门面完整；CLI `python -m muse.muse_commander`；Portal 未接（P1-03） |
| C 价值创造 | business_opportunities/unit_economics → video_* / product_builder → **publish_gate** → revenue_ledger | 发布准备包与账本分离已实现；与视频工厂产物尚未自动衔接（P1-06） |

## 3. 数据契约（本轮新增）
- `memory_epistemics(ref_kind, ref_id, track, epistemic_tag, origin, approval_status, approved_by, created_at)` —— 与 `memories` 同库侧表。
- Research Wiki 对象 = `knowledge_nodes.metadata.wiki_object ∈ {claim,source,evidence,experiment,decision,failure,alternative}` + `epistemic_tag/source/confidence/origin/is_evidence`；证据边 `supports/refutes` 由 evidence 节点指向 claim。
- 完成凭证 `receipts.jsonl`：`{task_id, plan_id, step_key, artifact_sha256, builder_id, reviewer_id, review_id, finished_at, prev_receipt_sha256, receipt_sha256}`。
- 发布准备包 `publish_manifest.json`（schema `muse.publish_ready_bundle.v1`，`publish_allowed=false`）。
- 静默吞错账本 `data/diag/suppressed_errors.jsonl`（或 `$MUSE_ERROR_JOURNAL`）。

## 4. 缝合断层地图
| 断层 | 位置 | 状态 |
|---|---|---|
| 两套派发路径（MuseCommander 门面 vs MuseRuntime） | unified_runtime.py | 🔴 P1-02 |
| Super Agent Party 无任何适配器代码 | src/muse | 🔴 P1-05（adapter_hub 诚实报 UNCONNECTED） |
| dsh 仅 vendored JS，无 Python 适配器 | dsh-our-free-model/adapter | 🔴 P1-05 |
| Research Wiki 原生只有 6 类节点，Evidence/Alternative 非一等节点 | research_knowledge.py | 🟡 已由门面补齐（以 idea 节点 + wiki_object 承载） |
| 全局无统一认知标签（仅玄学 2 标签 + business 3 标签） | 多处 | 🟡 epistemics.py 已建，其它模块尚未全部改用（P1-07） |
| 17 处 `except Exception: pass` | unified_runtime×6、status_api×2、lightpanda×3、worker、capability_radar、chatanywhere_smoke、contextual_inquiry_security、open_design、test_runner | 🟡 已全部改为 error_journal 落账 |
| 专家角色仅 8 个内置画像 | specialist_router.py | 🟡 expert_fleet.py 扩至 15 个并绑定执行器 |
