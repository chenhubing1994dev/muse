# B141｜Runner 恢复、价值创作台与全仓库回归验证

日期：2026-11-17
项目：`<MUSE_ROOT>`

## 本轮工作

- 恢复并复用既有 Muse WebCodex Workflow Session；Runner `administrator-72132063e1ed46f0` 在线，WebCodex Server/Runner 均为 `0.5.0`，协议兼容、源码对齐，Runner 声明并发上限 32，开始时无活动 Job。
- 检查既有工作区：`main` 分支，HEAD `5c469ba7e448487daef86694ecbae2834f6cade3`；保留原有 18 个未提交路径（10 个已跟踪修改、8 个未跟踪文件），本轮没有覆盖、清理或提交这些改动。
- 后续 review snapshot 观察到工作区已扩展为 27 个路径（13 个已跟踪修改、14 个未跟踪文件），包括 `daemon_control.py`、`pipeline_control.py` 与新增 UI API；这些变化不归因于本轮验证，按并发/外部工作区改动保留，未清理或提交。
- 尝试同步追加到主进度文件 `docs/PROGRESS.md` 时，编辑器因其历史混合/裸 CR 换行格式拒绝写入；事务未修改任何文件。本轮完整记录保存在本文件，主进度文件的格式修复应另行做字节级备份与专门迁移。
- 复核 B-140 价值创作台与桌面数据面板交付；评分器仍明确是本地启发式排序，不是流量或收益预测。

## 验证结果

| 验证 | 结果 |
|---|---:|
| `ui-prototype` Svelte/TypeScript `npm run check` | 0 errors / 0 warnings |
| `ui-prototype` Node 测试 | 36/36 passed |
| `ui-prototype` Vite production build | 119 modules；CSS 40.76 kB；JS 146.15 kB |
| Open Design adapter tests | 15/15 passed |
| Software crafting / MCP adapter tests | 36/36 passed |
| Worker / Commander tests | 30/30 passed |
| Status API tests | 26/26 passed |
| 全仓库 Python unittest（Python 3.14 + `.muse-test-deps`） | **1257/1257 passed**（131.093 秒） |

全仓库回归需同时将项目根目录与隔离依赖目录加入 `PYTHONPATH`，并将本机回环请求从代理中排除。第一次直接运行缺少 `tzdata`，第二次测试发现目录导入路径问题；修正测试环境变量/发现根路径后，最终全量回归 1257/1257 通过。这些是测试启动环境问题，不是通过跳过测试解决。

## 粤语 ASR 严格门槛复核

恢复并读取此前严格 CER 0.10 评估 Job `wc_job_ri0G3ZtO7np9NJL1`：

- 样本 1：Muse 被识别为 “WS”，CER 0.1875，`needs_review`。
- 样本 2：出现替换/插入错误，CER 0.125，`needs_review`。
- 样本 3：CER 0.00，机器门槛通过。
- 3 条样本均要求人工听审；机器 CER 不代表自然度、专有名词、发音或编辑质量已验收。

因此 ASR/SRT 代码与模型真实推理已接通，但默认自动旁白仍不应启用；还需人工听审，并在真实 Wan 画面上完成字幕同步、响度/真峰值与完整产品 QA。

## Open Design 当前边界

- 新增适配器及测试代码已有 15/15 通过；它通过 Muse 既有 TaskQueue、显式 Agent allowlist 与审批门工作，不建立第二套 Commander。
- 本机审计记录的安装版本为 0.25.0，而适配器预期目标为 0.27.1；本轮没有升级、启动 daemon、创建项目或执行 live MCP 写操作。
- 尚未完成真实 `tools/list`、本地 Agent 发现与端到端创作 smoke test。升级前需按审计方案备份整个 Open Design AppData（含 SQLite WAL），再执行签名/哈希/版本核验；不得把单元测试通过当作 live 集成完成。

## 下一步

1. 人工试听并修订粤语旁白样本，尤其是 Muse 专有名词与口语句式；对通过审听的语音生成字幕并检查时间轴。
2. 将审核后的粤语旁白/SRT 接入真实 Wan 生成片段，完成全片解码、音画同步、响度与 ZIP 完整性验收。
3. 按用户批准的升级窗口备份 Open Design 数据，升级并核验 0.27.1，再做只读 MCP 握手与本地 Agent 探测；写入/生成仍走 Muse 审批门。
4. 保留当前工作区全部既有改动，后续按文件分组 review、验证与提交，不做无关清理。

**本轮结论：Runner 已恢复；B-140 前端验证通过；Open Design/Worker/Status API 定向测试通过；全仓库 Python 1257/1257 通过。没有宣称 Open Design live 集成、粤语听感验收或真实 Wan 旁白成片已完工。**

## 并发变更架构风险（待处理）

- 已完成 B-142 架构修正：`PipelineController.submit()` 只向既有 TaskQueue 写入 `video_pipeline` typed ticket；移除独立 JSON job ledger 与私有 `threading.Thread` 调度。
- 新增 `PipelineTaskHandler`，仅执行固定 recipe allowlist；通过既有 Worker 的 lease/heartbeat/failure lifecycle 执行，设置 media workload 与 manual recovery policy。
- `AutonomousSupervisor.handlers()` 注册该 handler，且本地自动 tick 仅领取 `local_workflow` / `video_pipeline`，不会越权领取任意 ChatGPT/Ruflo ticket；独立 Worker CLI 也注册同一 handler。
- `PipelineController` 的 list/get API 从 TaskQueue 读取，`status_api` 显式绑定已有 task DB；任务账本统一，不再维护第二套状态源。
- TDD：先添加 7 项新测试，观察到缺少 `PipelineTaskHandler` 的预期导入失败，再实现最小改动；7/7 定向测试通过。
- 追加边界测试：先观察到 6 个较新的非视频 Ticket 会导致旧实现漏掉较早的视频任务（`IndexError`），再将 `TaskQueue.list_tasks(kind=...)` 改为 SQLite JSON payload-kind 精确过滤；新增历史过滤测试通过，管线测试现为 8/8。
- B-142 首轮全仓库回归 1260/1260 通过；历史过滤边界修复及第 8 项测试加入后，最终全仓库 Python unittest **1261/1261 通过**（130.409 秒）。
