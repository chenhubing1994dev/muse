# PROGRESS · B391 全链路融合（2026-11-27，AWS Quick 执行）

> 基线：分支 `b391-full-fusion`；`main` = `88250db`（未改动）。本轮未执行任何 git 命令，未提交、未推送。
> 每个已有文件首次修改前原件备份在 `.qw-backups/20261127-b391/`，SHA-256 见同目录 `MANIFEST.txt`；逐条改动见 `CHANGELOG.md` 中 `B391-*` 条目。

## 证据分层（必须先读）

| 层级 | 含义 | 本轮状态 |
|---|---|---|
| L1 静态 | AST 语法、JS 括号/字符串/模板结构、HTML id 与 CSP 规则核对 | ✅ 全部改动文件通过 |
| L2 云端沙箱真实执行 | 把真实模块（task_queue / memory / research_knowledge / xuanshu_* / 新模块）拼装执行，临时 SQLite | ✅ 控制 API 与玄学链路 57/57 |
| L3 真机单元/集成测试 | 在你的 Windows 上运行 `3_双击B391验收.bat` | ⏳ 未运行 |
| L4 真机样片 | 同一脚本 `--sample` 渲染一条真实本地动效样片入库 | ⏳ 未运行 |
| L5 路径 B 实机 | ComfyUI + GPU + 人工审批后的 Wan 镜头 | ⏳ 未验收（ComfyUI 当前未运行） |

在 L3/L4 通过前，本批次状态是：**代码与沙箱测试通过，实机待验收**。不宣称“智能创作 100% 稳定交付”。

## 一、审查修复（R1–R5）
详见 `reports/b391_review/B391_REVIEW_20261126-1940.md` 第六节回执。
- R1 健康检查：未配置存储注册表不再判为 503。
- R2 AI 时长：界面删除 1 秒选项，与后端 2–5 秒一致。
- R3 `api.js` 拼行拆回。
- R4 视频 Worker：FIFO、跳过领取前被拒的票、每 tick 最多一张；审批身份如实标注为本机单用户会话。
- R5 本地路径界面不再能组合出后端必拒的请求。

## 二、P0 视频双路径（保留另一程序已写的实现，并核对）
- 路径 A：`build_from_source(narration_script=...)` → CantoTTS 分段合成 → Whisper 粤语 ASR CER 门禁 → Remotion 动效 → FFmpeg → `package_generated_clip`（要求 TTS 合成溯源、旁白不得长于画面）。Remotion 失败抛 `VideoProductError`，不会静默降级。
- 路径 B：前端只走 `POST /api/v1/video-approval-requests` → 人工 `APPROVE-VIDEO-ONCE` → 唯一 `video_pipeline` 执行 Ticket → `dispatch_video_pipeline_once` → `run_approved_ticket`。旧直通入口仍 409。
- 新增 `GET /api/v1/control/creation/readiness`：按真实文件/服务探测选择路径，列出缺失能力（只查文件与服务，不启动模型）。

## 三、P1 交互控制 API（`src/muse/control_center_api.py`，挂载于 `/api/v1/control/*`）
同一 loopback + Origin + Bearer 鉴权；JSON ≤16 KiB、拒绝重复键；所有写操作需要确认短语且不会启动执行。

| 路由 | 说明 |
|---|---|
| `GET summary` | 门户汇总：队列、Wiki 节点分类、双轨记忆计数、最新视频、七节点循环统计 |
| `GET tasks` / `GET tasks/<id>` | 无载荷 Ticket 列表；详情含事件时间线与 DAG（若已绑定） |
| `POST tasks` | `SUBMIT-TO-COMMANDER`，幂等 request_id，恒为 `waiting_approval` |
| `POST tasks/<id>/cancel`、`/retry` | `CANCEL-TASK`；`RETRY-TASK` 生成新的待审批票（去掉内部 `_` 字段） |
| `GET approvals` | 统一审批中心：Commander 票、记忆候选、改进提案；专用门禁的票只显示入口 |
| `POST approvals/<id>/decide` | `APPROVE-TICKET` / `REJECT-TICKET`；视频审批、video_pipeline、签名审查、情境审查一律拒绝并指向专用门禁 |
| `GET memory?track=user|agent&q=` / `memory/<id>/history` / `memory/obsidian` | 双轨浏览检索、逐版历史（含软删除）、Obsidian 只读镜像哈希审计 |
| `POST memory/candidates/<id>/decide` | `APPROVE-MEMORY` / `REJECT-MEMORY` |
| `GET telemetry` | 真实 CPU 线程、内存、存储、GPU WAL 租约；并发硬上限 32；网络与 CPU 占用率未测量即显示未知 |
| `POST xuanshu/hypotheses` | 象征观察 → 清晰问题 → 3 条可检验假设与替代解释；`WRITE-HYPOTHESES` 写入 Wiki（idea + proposed claim，`inspired_by` 边） |
| `POST xuanshu/dispatch` | 仅允许带标签的假设 claim，生成待审批研究/样片验证 Ticket |

玄学标签（`src/muse/metaphysics_label.py`）：`/api/v1/metaphysics/*`、`/api/v1/omega/xuanshu/*`、`/api/v1/control/xuanshu/*` 的 2xx 响应统一加 `epistemic_display_label="玄学推演／待验证假设"`，并强制 `is_evidence=false`、`is_prediction=false`；写入 Wiki 的节点 metadata 同样携带。

## 四、P2 双视图界面（`ui-prototype/`，vite 多页入口，桌面网关与本机控制台均支持 `/portal`、`/os`）
- `portal.html` / `portal.js`：顶部导航与金框铭文、Hero + 竖排题词 + 星象八卦动态法阵、七节点循环（点击显示真实统计并跳转 OS）、五大能力卡（实时徽章）、真实成果（Product Vault 受控视频流 + SHA-256 + QA 徽章；无成片时只给“立即生成”按钮）、实时健康度圆环（8 项接口判定）、六阶段路线图（标注为规划）。
- `os.html` / `os.js`：HUD 框体、时钟、Tauri 窗口控制（仅在桌面壳内出现）、8 个模块全部接真实接口：
  总览（Commander + 6 角色星轨，状态只来自注册表心跳）、任务中心（列表/筛选/详情/DAG/新建/取消/重试/待审批门禁台：Commander 票、Video Admission、记忆候选、改进提案）、智能体（路由白名单、在线/阻塞、历史成功率、审查通过率）、记忆库（双轨切换、检索、逐版 Diff、候选审批、Obsidian 审计）、知识图谱（6 类节点着色、按关系过滤、`tested_by` 高亮、点击查看证据链与 claim 档案、从节点发起研究）、工具箱（智能创作入口按就绪度选路径、成片库与创作者反馈、玄学灵感引擎）、工作流（视频审批与执行票、产能账本、流水线）、设置（自治运行时、安全控制器暂停/恢复、遥测、GPU 租约、存储健康、Reviewer 密钥）。
- 设计图里的插画人物与写死的数字（92%、99.2% 等）没有照搬：数字全部来自接口，未知就显示未知。
- 主控台导航栏与 Omega 导航新增两个入口；页面无内联脚本/样式/事件处理器，符合 `test_omega_security_contract`。

## 五、新增测试
- `tests/test_b391_control_center.py`：控制 API、标签、Status API HTTP 接线（17 个用例）。
- `tests/test_status_api.py` +2（R1），`tests/test_autonomous_runtime.py` +2（R4）。
- `ui-prototype/muse-os-logic.test.js`：界面纯逻辑 12 个用例（`npm test` 自动包含）。

## 六、已知限制 / 未做
- 路径 B 实机验收需要你启动 ComfyUI，在 OS 工具箱提交并人工批准；脚本不会自动批准。
- ~~创作者反馈只写账本~~ → 已在 B391-P2-10 补齐同步 `tested_by` 回链（见第七节）。
- ~~`App.svelte` 第 128 行历史拼行~~ → 已在 B391-P2-09 修复。
- 本机每日 03:00 的 GitHub 自动同步任务会把工作区改动自动提交并推送；如不希望本批次在验收前被推送，请先暂停该计划任务。


## 七、B391-P2 第二轮饱和攻坚（2026-11-27 12:26）
证据层级：L1 静态 ✅ · L2/L2.5 云端沙箱真实执行 ✅ · L3/L4/L5 ⏳（需你双击验收）。

**L2.5 云端验真靶场**（工作区 `tmp/harness/`，不污染项目）：把 `src/muse` 真实源码按模块装载执行（沙箱禁止 sys.path/importlib，仅改写导入机制，不改业务逻辑），GPU/FFmpeg/子进程/Win32 API 以“工具不存在”桩隔离。
- 26 个 Python 测试套件、393 个用例：通过 333；60 个受沙箱限制无法执行（fsync、os.getpid、sys.modules 注入、子进程、跨测试模块导入、网络盘 I/O 超时等，逐项归因见 CHANGELOG/回执），**无法解释的失败 0**。
- 前端纯逻辑 `muse-os-logic.test.js`：17/17，68 个断言（云端 Node）。
- 契约核验 `scripts/b391_l25_contract.py`：真实 Status API 上回放 os.js + portal.js 全部 60 个调用点，未知路由 0；界面使用的 10 个确认短语全部有后端精确校验（另有 2 个仅界面防误触短语：暂停/恢复自治，已在界面标注）；DOM 引用 100 个、缺失 0、重复 0；CSP：内联 script/style/on* 均为 0。

**对抗自审修复**：JSON 非有限数/深度、并发幂等双 201、重试串票/丢 workload/剥签名门禁、Obsidian 计数丢失与 BOM 误报、成片无 Range（无法拖动进度）、网关整段缓冲 2 GiB 视频、门户轮询打断播放、就绪度缓存永不过期、拼行 14 处（含 App.svelte 第 128 行）。详见 CHANGELOG `B391-P2-01…28`。

**七节点最后闭环**：成片库反馈可关联待验证假设 → `POST /api/v1/control/creation/feedback`（`LINK-FEEDBACK-TO-CLAIM`）同时写创作账本与 Research Wiki `claim —tested_by→ 实测反馈记录`；知识图谱 claim 抽屉分栏显示“原始假设来源（含玄学标签）”与“实测反馈（时间、成片 SHA-256、创作者结论）”，claim 仍为 proposed、不被自动改写。真实 HTTP 端到端用例覆盖：玄学观察 → 假设写入 Wiki → 派发待审批样片票 → 成片反馈回写 tested_by → 图谱可查。

**Windows 装甲**：验收脚本每步原子落盘 `reports/b391_acceptance/LATEST_REPORT.{json,md}`（环境快照、每步耗时、完整失败堆栈、输出末尾、样片 SHA-256）；bat 显式 UTF-8；npm 测试不再依赖通配符展开；测试临时目录容忍 WinError 32。路径 B 作战卡：`docs/B391_L5_PATH_B_RUNBOOK.md`。
