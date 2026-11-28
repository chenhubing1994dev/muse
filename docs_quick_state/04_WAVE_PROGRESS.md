# 04 · 波次进度与精确断点

## 当前断点
- **最新：全自主 T1–T10（2026-11-27 22:41–23:1x）**。经监听器自跑诊断 12 次；最终结果见 `reports/diag_latest.json`。新增 AC-049/076/077/084/035/015/036/093，共 62 条。详见 docs/AUTONOMY_STATE.md、docs/DECISIONS.md D-006…D-017。
- **最新：Wave 2 第 3 轮（2026-11-27 22:4x CST）**。第 5 次实测（22:22）：**探针 32/0/0/0，AC 53/53 全绿**（首次完全通过）。本轮新增 Workflow A 统帅主干 `workflow_a.py` + L5.A2（AC-048）。AC 总数 54。等待第 6 次 `run_diag.bat`。
- **最新：Wave 2 第 2 轮（2026-11-27 22:3x CST）**。第 4 次实测（22:17）：探针 29/2/0/0，AC 50/52；根因 R6（Kill 两步解除缺 resume）、R7（治理探针发现 6 个未登记标签）均已修。新增 AC-014（对话→双轨记忆）。AC 总数 53。等待第 5 次 `run_diag.bat`。
- **最新：Wave 2 第 1 轮（2026-11-27 22:3x CST）**。第 3 次实测（22:08）：探针 28/0/1/0，AC 48/49；唯一错误 = Kill 复位顺序（已修）。本轮新增：控制台 CommanderPanel（AC-083）、视频包→发布准备包桥接（AC-075）、认知标签治理（AC-047）。AC 总数 52。等待第 4 次 `run_diag.bat`（会在本地执行一次 `npm run build`，约 1 分钟）。
- 以下为上一轮记录：
- 波次：**Wave 1 第 3 轮 / Wave 2 起步**（2026-11-27 22:1x CST）。
- 第 2 次实测（21:55）：探针 26 PASS / 1 FAIL / 0 ERROR；AC 45 PASS / 2 FAIL（AC-080、AC-082，同一根因）。
- 本轮：修 POST 挂载；清剿 24 处“吞错返回空值”；L1.3 规则升级；ResearchPipeline 接 Wiki 契约 + 新探针 L5.R（AC-046）。AC 总数 48。
- 等待用户第 3 次双击 `run_diag.bat`。
- 下一轮：① 处理第 3 次结果 ② P1-02：MuseRuntime.dispatch_commander_once 审批/派发收口到 MuseCommander ③ ui-prototype 审批/Kill 按钮 ④ P1-06 视频产物 → publish_gate。

## 实测历史
| 次 | 时间 | 探针 | AC | 根因簇 |
|---|---|---|---|---|
| 1 | 21:26 | 22/1/4/0 | 31/15 | R1 分页上限 · R2 扫描边界 + 真实语法错误 · R3 Wiki 导出白名单 |
| 2 | 21:55 | 26/1/0/0 | 45/2 | R4 status_api POST 挂载未落盘（写入通道冲突） |
| 3 | 22:08 | 28/0/1/0 | 48/1 | R5 Kill 复位顺序写反（安全控制器正确拒绝） |
| 4 | 22:17 | 29/2/0/0 | 50/2 | R6 Kill 两步解除缺 resume · R7 治理探针新发现 6 个私有标签 |
| 5 | 22:22 | 32/0/0/0 | 53/0 | 全绿 |

## 待你拍板
1. Super Agent Party / dsh 是否接入（需运行方式或仓库地址）。
2. 旧 User Track 记忆批量标 `user_confirmed` 还是逐条复核。
3. L6 是否允许一次本地模型最小推理（LM Studio 未运行 → SKIPPED_ENV）。
