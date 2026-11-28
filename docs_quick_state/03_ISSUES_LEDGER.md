# 03 · 缺陷总账（P0 阻断崩溃 / P1 断层或假活 / P2 健壮性）

## P0
- 无已知 P0（全仓语法 0 错误为 2026-11-27 静态结论；待 L1/L2 首跑实证）。

## P1
| ID | 问题 | 处理 | 状态 |
|---|---|---|---|
| P1-01 | 17 处 `except Exception: pass` 静默吞错（unified_runtime×6、status_api×2、lightpanda_adapter×3、worker、capability_radar、chatanywhere_smoke、contextual_inquiry_security、open_design、test_runner） | 新增 `error_journal.record_suppressed_error`，全部改为结构化落账（日志 + JSONL + 计数） | 🟡 待 L1.3/L2 验证 |
| P1-02 | 两套派发路径：MuseCommander 门面 与 MuseRuntime.dispatch_commander_once；AutonomousSupervisor 的 SafetyController 默认未挂载，Commander 的 Kill 对它们无效 | 新增 `commander_gate.py` 全局门（KILL_SWITCH 文件 + Commander 安全态只读检查），两条外部路径入口强制检查（AC-034）；Ruflo DAG 内部执行逻辑仍独立（683 行，视频线并发维护，未整体替换） | 🟡 入口收口 / 🔴 内部统一 |
| P1-03 | Portal/桌面端无 Commander 审批与 Kill 按钮直连 | 第 2 次实测 POST 全部 405：POST 挂载块未落盘（同文件连续两次编辑，第二次被旧副本覆盖）→ 已改设备端精确插入并回读校验；已新增 `commander_api.py` 并挂入 status_api（GET status/approvals/capacity/adapters/fleet，POST approve/cancel/kill，需显式确认+人类身份）；ui-prototype 按钮未接 | 🟡 后端+前端面板（CommanderPanel，AC-083） |
| P1-04 | Research Wiki 七类对象门面未被生产入口调用 | ResearchPipeline 写入的来源已带 Wiki 契约（wiki_object/fetch_status 只升不降/认知标签/摘录+哈希），AC-046 探针覆盖；Claim/Evidence 仍由 research_closed_loop 另行写入，未统一 | 🟡 部分 |
| P1-05 | Super Agent Party 无适配器；dsh 无 Python 适配器 | adapter_hub 诚实报 UNCONNECTED；需你确认是否接入（见 04 待拍板） | 🔴 |
| P1-06 | 视频工厂产物未自动进入 publish_gate 发布准备包 | 新增 `publish_gate.bundle_from_video_package`（mp4/srt/jpg 结构校验，缺必需件判无效，不改动视频线原包）+ AC-075；视频线完成时自动调用尚未接（视频线由另一 Agent 并发维护） | 🟡 桥接 / 🔴 自动触发 |
| P1-07 | 认知标签未全局统一（46 个私有标签字面量分布在 25 个模块） | `epistemics.LEGACY_TAG_MAP` 全部映射 + 禁止升级规则 + L1.5 治理探针（新增未登记私有标签即 FAIL）；API 响应层尚未统一附加 `epistemic_tag`（会改动 3,048 条既有测试期望的 JSON，需评估） | 🟡 映射 / 🔴 响应层 |
| P1-08 | 旧 User Track 数据无认知标签侧表记录 | `contamination_audit()` 会列为 unstamped_legacy，需你决定批量标记为 user_confirmed 还是逐条复核 | 🟡 待拍板 |

## P2
| ID | 问题 | 状态 |
|---|---|---|
| P2-01 | 本机 `.venv` 启动器 `No pyvenv.cfg`（历史问题）；run_diag/start_muse 已自动回退 | 🟡 |
| P2-06 | R1 新门面越过存储分页上限（已修，改 SQL 聚合） | 🟡 |
| P2-07 | `artifacts/a_share_20261127/qa_package.py` 语法错误（另一 Agent 今日写入，已拆行修复） | 🟡 |
| P2-02 | `"%~dp0"` 结尾反斜杠会转义引号 → 新脚本统一用 `"%~dp0."` | 🟡 |
| P2-03 | L6 真实推理探针：只做 `/v1/models` 连通，不做推理（避免占 GPU），`MUSE_DIAG_LIVE_INFERENCE=1` 预留 | 🟡 |
| P2-04 | `.git/config` 历史明文令牌（QUICK_STATE 已记）需你在 GitHub 吊销 | 用户操作 |
| P2-05 | 每日 03:00 自动同步会提交并推送工作区改动（含本轮新文件） | 用户知悉 |
| P1-09 | 24 处 `except Exception:` 后直接 `return/continue/赋空值`（L1.3 原规则只查 `pass`，漏检）：worker 租约心跳×3、research_pipeline 知识召回、unified_runtime Ruflo 状态/配置、providers 额度路由×2 等 | 全部在 handler 首行注入 `record_suppressed_error`（原降级语义不变）；L1.3 规则升级覆盖 pass/continue/空返回/空赋值 | 🟡 |
| P2-08 | 工具链：`file_edit` 与设备端写入/读取存在同步延迟，曾导致 status_api POST 挂载丢失 | 同一文件只用一个写入通道，写后回读校验 | 流程已改 |
| P2-09 | Kill 复位顺序：SafetyController 要求先删 KILL_SWITCH 文件再复位，CLI 与探针写反（第 3 次实测唯一错误） | 已修正顺序，并新增断言“只删文件不得重开” | 🟡 |
| P2-10 | Kill 解除为两步（reset→paused，resume→running），CLI/探针缺 resume（第 4 次实测 L4.9） | CLI `reset-kill` 改为复位后显式 resume；探针断言“仅 reset 仍暂停” | 🟡 |
| P2-11 | 治理探针新发现 6 个私有标签（website_knowledge 检索角色 4 个、philosophy 2 个；`epistemic_role` 键未纳入扫描） | 已映射（全部非证据级：候选→hypothesis，opinion/自称 evidence→interpretation），扫描键加入 epistemic_role | 🟡 |
| P1-10 | 对话抽取的用户事实未经双轨门打印章；抽取器异常 `except Exception` 吞掉原因 | MuseAssistant 绑定 DualTrackMemory：候选=agent_inference 待确认；异常落账；chat_cli 关闭句柄（AC-014） | 🟡 |
| P1-11 | Workflow A 各段（inspiration/research/product）由各自入口直接调用，不经 Commander 票据/审批/审查/凭证 | 新增 `workflow_a.py`：5 段编排为 Commander DAG，步骤间以磁盘状态文件交接，进化段强制人工门（AC-048）；旧入口（status_api 灵感接口等）仍可直调，尚未改为提交票据 | 🟡 新主干 / 🔴 旧入口 |
| P1-12 | T2–T8 收口项（接口绕过、Workflow C、视频交付包、接口标签、Ruflo 直执行、旧记忆标签、外部适配器） | 全部实现并经诊断实测（见 04 与 diag_latest.json） | 🟢 |
| P2-12 | 既有 pytest 用例可能因响应体新增字段/审批前 task_id=null/Ruflo 票据守卫而失败（未运行） | 见 docs/KNOWN_ISSUES.md KI-005 | 🟡 待验证 |
