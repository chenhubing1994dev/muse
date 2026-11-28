# 00 · 验收标准矩阵（共 75 条）

> 每条 AC 由 `scripts/muse_diag.py` 中同编号探针自动断言；宣判结果只认 `reports/muse_diag/latest.json`。
> 判定：`PASS` = 映射探针全部通过；`SKIPPED_ENV` = 缺本机环境（不算通过、不算失败）；`FAIL/ERROR` = 未通过；`NOT_RUN` = 无探针结果。
> 范围：Phase 0～4。Phase 5～6（文明级扩展）不列 AC，见 02 矩阵 ⚪。

| AC | 版图/主干 | 验收断言（自动） | 层 |
|---|---|---|---|
| AC-001 | 底座 | 全仓（排除黑名单目录）`.py` AST 解析 0 错误 | L1 |
| AC-002 | 底座 | `config/`、`web-portal/` 的 JSON/TOML/HTML 结构有效；JS 在有 node 时 `node --check` 通过 | L1 |
| AC-003 | 底座 | `src/muse` 无 `except Exception/BaseException/裸 except: pass` 静默吞错 | L1 |
| AC-004 | 底座 | 根目录诊断/启动 `.bat` 含 `chcp 65001`、`cd /d "%~dp0"`，Python 路径带引号 | L1 |
| AC-005 | 底座 | `src/muse` 全部模块可导入（缺依赖/循环导入/语法均记为失败） | L2 |
| AC-006 | 底座 | 任一模块导入耗时 < 8 s（无顶层阻塞副作用） | L2 |
| AC-007 | 底座 | 含空格+中文的路径下 SQLite 与文件 UTF-8（含 emoji/生僻字）无损往返 | L3 |
| AC-008 | 底座 | 二次失败结构化落账：`error_journal` 写入 JSONL 且计数可读 | L3 |
| AC-010 | ②双轨记忆 | Agent 推断直写 User Track 被拒（`UserTrackContamination`） | L3 |
| AC-011 | ②双轨记忆 | Agent 推断仅能提案；`agent:` 身份确认被拒，`user:` 确认后才入 User Track | L3 |
| AC-012 | ②双轨记忆 | 记忆带来源/认知标签/置信度/时间戳/修订链（revise 后 history ≥ 2）；底层 `MemoryStore.approve_candidate` 对 User Track 候选只接受 user:/human:（agent:/无前缀被拒且零写入） | L3 |
| AC-013 | ②Obsidian | 已批准记忆导出到 Vault 幂等（两次导出文件哈希一致），预置的用户手写笔记字节不变 | L3 |
| AC-014 | ②双轨记忆接入对话 | `MuseAssistant` 模型抽取的用户事实只作为 pending 候选，带 `agent_inference` 印章，未确认不可检索；抽取器崩溃被结构化落账 | L3 |
| AC-015 | T7 旧记忆迁移 | 无标签 User Track 记忆（含软删除）一律标 `legacy_unverified`（绝不标为用户已确认）：先完整备份（哈希可验）、仅新增侧表行、`memories/memory_revisions` 内容哈希与条数不变、重复运行 0 新增、回滚只删本次新增行、报告落盘；真实库已迁移且备份完好 | L3 |
| AC-020 | ①统帅 | Commander 按 DAG 依赖顺序执行（下游不早于上游） | L4 |
| AC-021 | ①统帅 | 高风险步骤自动 `waiting_approval`，未批准时 drain 不执行 | L4 |
| AC-022 | ①统帅 | 非人类身份（`agent:`/`worker`）批准被拒 | L4 |
| AC-023 | ①统帅 | Kill Switch：派发被阻断，未启动票据全部取消 | L4 |
| AC-024 | ①统帅 | 超时熔断：超时 handler 判失败并发出取消信号 | L4 |
| AC-025 | ①统帅 | 独立审查：builder=reviewer 被拒；哈希不符的产物被驳回 | L4 |
| AC-026 | ⑦成本主权 | 未知/未验证免费额度 → 付费云调用被阻断（默认不开付费） | L4 |
| AC-027 | ①资源感知 | CPU/RAM 实测；GPU 未探测时诚实为 `unknown`；给出并发上限 | L4 |
| AC-028 | ①适配器 | 无 Commander 授权的适配器调用被拒 | L4 |
| AC-029 | ①专家舰队 | 15 个专家角色注册入库，执行器绑定静态存在 | L4 |
| AC-031 | Workflow B | 完成凭证哈希链可验证 | L5 |
| AC-032 | ⑦成本账本 | 每次模型/工具调用写入本地成本账本 | L5 |
| AC-033 | Workflow B | 端到端：立项→审批→执行→审查→归档，状态差分落盘 | L5 |
| AC-034 | ①统帅收口 | 全局 Kill Switch：Commander 按下后，`MuseRuntime.dispatch_commander_once`（Ruflo DAG）与 `AutonomousSupervisor.run_tick` 同样被阻断；复位后恢复 | L4 |
| AC-035 | T6 Ruflo 收归统帅 | `RufloMCPAdapter.call_tool` 对执行型工具强制票据作用域：无票据/票据未运行/租约属他人/全局 Kill 时一律 `BypassCommanderError` 并审计；只读工具不受影响；`initialize_swarm(approved)` 作为 Commander 票据执行（凭证链）；AST 校验 unified_runtime 中所有执行型调用都在票据绑定方法内 | L4 |
| AC-036 | T8 外部智能体适配器 | `external_agents`：dsh 与 Super Agent Party 统一 health/capabilities/invoke/timeout；只允许 loopback；外发走字段/角色/长度白名单；invoke 必须在 `route.external_invoke` 运行中票据内（高风险、`agent:` 批准被拒）；**契约测试（本地假服务端，非集成测试）**；真实健康：SAP 必须诚实为 not_configured | L4 |
| AC-037 | 待办 A 全入口审计 | 从源码枚举 status_api POST 块、control 路由、例程、Worker 处理器、视频钩子、console script 与 `python -m` 模块：全部须在 `entrypoint_registry` 登记（无过期项）；声明 commander_ticket / approval_gated / kill_switch_gated / legacy_ticket 的入口必须有对应源码证据；debt 数 ≤ DEBT_CEILING（只减不增） | L4 |
| AC-038 | 守护进程控制收口 | `/api/v1/daemon/start|resume|once` 每次创建 Commander 票据（全局停止时 409、状态不变）；`stop|pause` 作为安全动作始终可用并写 `safety_actions.jsonl`；放开后 resume 成功且有完成凭证 | L5 |
| AC-039 | 统一停止开关 + 雷达/回滚收口 | Omega 面板 Kill 也关闭 Commander 全局门且阻断 Commander 票据；`/api/v1/radar/scan` 经票据（含凭证）；`/api/v1/omega/evolution/rollback` 返回 202 pending_approval（高风险，批准前不改文件），`agent:` 批准被拒，`user:` 批准后原子回滚 | L5 |
| AC-040 | ③认知标签 | 八类认知标签：非法标签被拒；象征/传闻/虚构不可标为证据 | L4 |
| AC-041 | ③Research Wiki | 七类对象（Claim/Source/Evidence/Experiment/Decision/Failure/Alternative）全部落盘可计数 | L5 |
| AC-042 | ③Research Wiki | 待抓 URL 不能作为证据 | L5 |
| AC-043 | ③Research Wiki | 同源转载不算独立印证；无证据时输出 `insufficient_evidence` | L5 |
| AC-044 | ③象征实验室 | 易经/紫微/奇门/六壬融合输出强制标签，篡改为证据被拒 | L5 |
| AC-045 | Workflow A | 灵感→象征假设→Wiki 提案 Claim（不得为 supported）→Obsidian Wiki 导出 | L5 |
| AC-046 | ③Research Wiki 生产接线 | 生产 `ResearchPipeline` 写入的来源带 Wiki 契约：搜索命中=`pending_fetch`、已读页=`read`+摘录+哈希；未读命中不能挂证据 | L5 |
| AC-047 | ③认知标签治理 | 全仓 `epistemic_status` 等私有标签字面量全部映射到八类标签；含 not_/unverified/only 的旧标签不得映射为证据级 | L1 |
| AC-048 | Workflow A 统帅收口 | `workflow_a.run_workflow_a`：5 步全部为 Commander 票据；前 4 步自动完成并落盘（象征假设→Wiki、离线诚实 `insufficient_evidence`、Agent Track 经验、静态原型哈希校验、Obsidian 只新建）；进化提案高风险挂起，`agent:` 批准被拒、`user:` 批准后只生成草案不应用补丁；完成凭证链 5 条可验 | L5 |
| AC-049 | T2 接口收口 | `POST /api/v1/inspirations`、`/inspirations/evolve`、`/control/xuanshu/hypotheses`(写 Wiki) 均创建 Commander 票据并在服务内执行；视频/产品进化返回 `pending_approval`，`agent:` 批准 403、`user:` 批准后执行；磁盘有票据(succeeded)、完成凭证链、成本账本行、结果文件；直接调用核心函数抛 `BypassCommanderError` 且写审计、不写数据 | L5 |
| AC-050 | ④守护 | 到期提醒可被巡检检出 | L5 |
| AC-051 | ④受控进化 | 进化版本记录 + 一键原子回滚恢复原文件字节 | L5 |
| AC-052 | ④受控进化 | 进化提案按高风险挂起，须人工批准后执行 | L5 |
| AC-060 | ⑤插件治理 | 非法插件 Manifest 被拒 | L4 |
| AC-061 | ⑤插件治理 | 单个 handler 崩溃被隔离，后续票据继续执行 | L4 |
| AC-070 | ⑥多媒体 | WAV 产物文件头/时长校验通过；0 字节/伪造头被拒 | L5 |
| AC-071 | ⑥多媒体 | 有 FFmpeg 时真实合成 MP4 并通过 `ftyp` 校验（无则 SKIPPED_ENV） | L6 |
| AC-072 | Workflow C | 发布准备包停在人工门；非人类批准被拒；`publish_allowed=false` | L5 |
| AC-073 | ⑦收益账本 | 曝光信号不计收入；无回执的“结算”被拒；净收益=0 | L5 |
| AC-074 | ⑦商业 | 单位经济模型要求反证据（缺反证据被拒） | L5 |
| AC-075 | Workflow C 视频桥接 | 真实 FFmpeg 生成的视频包（mp4+srt+jpg）经 `bundle_from_video_package` 生成合规发布准备包；缺字幕/伪造视频被判无效 | L6 |
| AC-076 | T3 Workflow C 统帅主干 | `workflow_c.run_workflow_c`：机会(反证据+单位经济模拟，非预测)→原型+说明(哈希)→成本(仅统计真实记账行)→本地交付包(publish_allowed=false)→收益回写(无回执=unknown、收益账本 0 条、仅 Agent Track)均为 Commander 票据；“人工发布确认”高风险挂起，`agent:` 被拒，`user:` 批准后仅标记人工发布、Muse 不外发；凭证链 6 条 | L5 |
| AC-077 | T4 视频完成→交付包 | `video_task_worker` 两条成功提交路径均调用 `trigger_publish_bundle_after_video`（AST 校验）；真实 FFmpeg 视频包经该钩子生成 Commander 计划（复用 Workflow C 的收益/发布处理器）：交付包 3 件有效、publish_allowed=false、发布挂起、凭证链 2 条、重复触发幂等；缺字幕包→交付步骤 failed | L5 |
| AC-080 | ⑧Portal | status API 瞬态启动：`/api/v1/health` 带令牌 200、无令牌被拒 | L6 |
| AC-081 | ⑧Portal | `web-portal/index.html` 与 JS/CSS 存在且结构有效 | L6 |
| AC-082 | ⑧Portal/①统帅 | `/api/v1/commander/*`：待审批列表可见；`agent:` 批准 403、`user:` 批准 200；Kill 无确认 400、有确认 200 且安全态=killed | L6 |
| AC-083 | ⑧控制台统帅面板 | `CommanderPanel` 已挂载；前端客户端单测通过（拒绝非人类身份/非法票据号）；本地构建产物包含审批与 Kill 路由 | L6 |
| AC-084 | T5 API 认知标签 | 从 status_api/control/commander/omega 源码枚举全部 `/api/v1/*` 路由并逐个请求：每个 JSON 响应（含错误）都带 `epistemic_tag`∈八类 + 中文标签；玄学路由恒为 symbolic_inspiration；非 JSON 仅允许 SSE | L6 |
| AC-090 | 实连 | 主机资源实测（CPU/RAM/磁盘），未知项不编造 | L6 |
| AC-091 | 实连 | 适配器/本地模型四态诚实（LIVE_CONNECTED/CONTRACT_ONLY/SKIPPED_ENV/UNCONNECTED） | L6 |
| AC-092 | ⑧教会使用 | `start_muse.bat`、`run_diag.bat`、`docs/USER_GUIDE_LIVE.md` 存在且非空 | L7 |
| AC-093 | T9 本地模型最小推理 | nvidia-smi 测显存（不足/不可测则 SKIP+原因）；仅使用已加载模型（拒绝触发 JIT 加载/下载）；单次 `max_tokens≤16`、超时 120s；非空回复；推理后显存回落（容差 512MiB）；用量写本地成本账本 | L5 |
| AC-094 | P0-3 探针灵敏度 | 对 L5.A2/L4.1/L5.A3/L5.C1/L5.C3/L6.7/L4.10/L3.6/L4.11 各注入真实缺陷（复制源码到临时目录后替换唯一锚点），对应探针在子进程中运行必须为 FAIL/ERROR；锚点缺失即失败 | L7 |
| AC-095 | P0-4 台账一致性 | AC 编号唯一、每条 AC 至少被一个探针引用、探针引用的 AC 均存在、探针编号唯一、标题“共 N 条”与表格行数一致 | L7 |
| AC-096 | 待办 D 红线静态审计 | L1.6 扫描 src/scripts/tests：shell=True、pip/npm install、git push、src 内非本机网络调用模块、Muse 目录外写删、User Track 直写/候选晋升点；不在 `redline_policy` 白名单即失败（警报线，非沙箱）；L7.5 注入 3 类违规必须变红 | L1 |
| AC-097 | 待办 C 审计链 | 越权审计/安全动作/Ruflo 调用/成本记账均为哈希链 JSONL；`python -m muse.audit verify` 校验完成凭证链、各审计链及成本账本与链逐行一致；改写/删除/重排凭证、审计行、成本链或账本行共 7 种篡改全部报错，CLI 退出码干净 0、篡改 1 | L5 |
| AC-098 | 待办 B 故障注入与恢复 | 真实 Commander/TaskQueue：领取后崩溃（租约过期）只执行一次；已写凭证未结票崩溃 → 从凭证恢复、不重跑、凭证与成本不重复；高风险票据崩溃 → 回到 waiting_approval；凭证链尾部半写 → 新票据 failed（receipt_chain_corrupt）不抛异常；同计划 4 路并发提交得同一票据，3 路并发执行仅 1 次且成本 1 行；截断的工作流状态文件 → 下一步失败关闭；状态文件改为原子替换写 | L5 |
| AC-099 | 待办 G 性能基线与预算 | L6.8 测量单票据开销中位数、/api/v1/health p50/p95（30 次）、成本账本 500 行读取中位数，并满足绝对上限；diag_auto 性能预算阶段：前 3 次形成冻结基线，此后任一指标 >1.5×基线且超绝对容差即总体失败 | L6 |
| AC-100 | 待办 F 手册可执行性 | 从 docs/USER_GUIDE_LIVE.md 提取所有 `python -m muse.*` 与 `python scripts\*.py` 命令：逐条 `--help` 退出码 0，文档中的 `--参数`、子命令（含 a|b|c 选项）必须出现在帮助输出中；文档提到的 .bat 必须存在 | L7 |
| AC-101 | 待办 K 旧记忆复核身份门 | `legacy_memory_review`：只有 `user:<名字>` 能把 `legacy_unverified` 升为 `user_confirmed`；agent:/human:/空名/无前缀被拒且零写入（含 CLI 退出码 3）；非 User Track 标签与 Agent Track 记忆被拒；确认前备份（哈希可验）、仅侧表印章+复核账本变化、原文哈希不变、幂等、user: 可精确撤销；真实库复核账本无非 user: 复核人 | L3 |
| AC-102 | 待办 K Research claim 状态机 | `evidence_split` 只计本 claim 的 evidence 对象（键/立场/claim 一致、来源已读取且有 sha256、存在 claim-evidence 候选行）；伪造 supports 边、他 claim 证据、未读来源伪造件一律忽略并计入 ignored_edges；单一来源不得 supported，两独立已读来源才 supported；自身证伪失败阻止 supported；claim 状态不能经 upsert 直写；`transition_claim` 的实验证据须为带 raw_evidence_sha256 且结果与目标状态匹配的已完成运行 | L5 |
| AC-103 | 待办 J-1 深度健康检查 | `muse.deep_health.build_deep_health` 与 `GET /api/v1/health/deep`（需鉴权）只读汇总：统帅票据积压与待审批数、`muse.audit.verify` 审计链、账本大小、磁盘余量、Kill 开关、`reports/diag_latest.json` 新鲜度与结果、PENDING_HUMAN 条数；审计链被篡改、诊断报告损坏或非 PASS、磁盘低于下限、任一检查抛错 → `unhealthy` 且 HTTP 503（fail closed）；诊断过期/Kill/待审批超限 → `degraded`；运行前后工作区文件哈希不变 | L6 |
