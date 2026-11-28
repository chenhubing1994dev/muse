# 00 · 验收标准矩阵（AC-001 ~ AC-092）

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
| AC-012 | ②双轨记忆 | 记忆带来源/认知标签/置信度/时间戳/修订链（revise 后 history ≥ 2） | L3 |
| AC-013 | ②Obsidian | 已批准记忆导出到 Vault 幂等（两次导出文件哈希一致），预置的用户手写笔记字节不变 | L3 |
| AC-014 | ②双轨记忆接入对话 | `MuseAssistant` 模型抽取的用户事实只作为 pending 候选，带 `agent_inference` 印章，未确认不可检索；抽取器崩溃被结构化落账 | L3 |
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
| AC-040 | ③认知标签 | 八类认知标签：非法标签被拒；象征/传闻/虚构不可标为证据 | L4 |
| AC-041 | ③Research Wiki | 七类对象（Claim/Source/Evidence/Experiment/Decision/Failure/Alternative）全部落盘可计数 | L5 |
| AC-042 | ③Research Wiki | 待抓 URL 不能作为证据 | L5 |
| AC-043 | ③Research Wiki | 同源转载不算独立印证；无证据时输出 `insufficient_evidence` | L5 |
| AC-044 | ③象征实验室 | 易经/紫微/奇门/六壬融合输出强制标签，篡改为证据被拒 | L5 |
| AC-045 | Workflow A | 灵感→象征假设→Wiki 提案 Claim（不得为 supported）→Obsidian Wiki 导出 | L5 |
| AC-046 | ③Research Wiki 生产接线 | 生产 `ResearchPipeline` 写入的来源带 Wiki 契约：搜索命中=`pending_fetch`、已读页=`read`+摘录+哈希；未读命中不能挂证据 | L5 |
| AC-047 | ③认知标签治理 | 全仓 `epistemic_status` 等私有标签字面量全部映射到八类标签；含 not_/unverified/only 的旧标签不得映射为证据级 | L1 |
| AC-048 | Workflow A 统帅收口 | `workflow_a.run_workflow_a`：5 步全部为 Commander 票据；前 4 步自动完成并落盘（象征假设→Wiki、离线诚实 `insufficient_evidence`、Agent Track 经验、静态原型哈希校验、Obsidian 只新建）；进化提案高风险挂起，`agent:` 批准被拒、`user:` 批准后只生成草案不应用补丁；完成凭证链 5 条可验 | L5 |
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
| AC-080 | ⑧Portal | status API 瞬态启动：`/api/v1/health` 带令牌 200、无令牌被拒 | L6 |
| AC-081 | ⑧Portal | `web-portal/index.html` 与 JS/CSS 存在且结构有效 | L6 |
| AC-082 | ⑧Portal/①统帅 | `/api/v1/commander/*`：待审批列表可见；`agent:` 批准 403、`user:` 批准 200；Kill 无确认 400、有确认 200 且安全态=killed | L6 |
| AC-083 | ⑧控制台统帅面板 | `CommanderPanel` 已挂载；前端客户端单测通过（拒绝非人类身份/非法票据号）；本地构建产物包含审批与 Kill 路由 | L6 |
| AC-090 | 实连 | 主机资源实测（CPU/RAM/磁盘），未知项不编造 | L6 |
| AC-091 | 实连 | 适配器/本地模型四态诚实（LIVE_CONNECTED/CONTRACT_ONLY/SKIPPED_ENV/UNCONNECTED） | L6 |
| AC-092 | ⑧教会使用 | `start_muse.bat`、`run_diag.bat`、`docs/USER_GUIDE_LIVE.md` 存在且非空 | L7 |
