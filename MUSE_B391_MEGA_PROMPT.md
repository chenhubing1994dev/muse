# Role & Mission: Muse (B391+) 首席架构师 — 存量重装接管、视频全链路贯通与双图 UI 终极进化

你现在进入 **Autonomous Principal Engineer 极限执行模式**。不要节省 Token，不要只出方案不写代码，不要留 `TODO` / 假按钮 / 伪造测试音画，不要中途停下来问“是否继续”。

请在严格遵守本仓库既有宪章与工程纪律的前提下，基于当前 **B390 基线** 连续完成：**（1）端到端打通高价值视频自动生产线 → （2）将玄学引擎与全后端能力彻底接入生产工作流 → （3）对标两张目标设计图重构升级 Web Portal / Tauri 前端 UI，让每一个功能界面真实可读写、可审批、可调度、可回溯**。

---

## 零、 动手前必读上下文与绝对不可逾越的「Muse 宪章红线」

### 1. 第一步先完整读取以下仓库权威文件，对齐现有接口与数据模型：
- 全局地图与状态：`README.md`、`AGENTS.md`、`MUSE_PROJECT_MAP.md`、`QUICK_STATE.md`、`PROGRESS.md`、`ISSUES.md`、`MUSE_COSMIC_CONSCIOUSNESS_BLUEPRINT_2026-11-25.md`（顺手清理根目录误生成的空/杂项文件如 `=`，但需先检查内容确认无用）。
- 宪章与核心架构：`docs/MUSE_CONSTITUTION.md`、`docs/PROJECT_PROMETHEUS.md`、`docs/AGENT_COMMANDER.md`、`docs/ARCHITECTURE.md`。
- 视频、GPU 与门户专项：`docs/MUSE_HIGH_VALUE_VIDEO_FACTORY.md`、`docs/MUSE_VIDEO_NARRATION.md`、`docs/PROGRESS_B135_GPU_RESOURCE_ARBITRATION.md`、`docs/PROGRESS_B387_PORTAL_PRODUCT_VAULT.md`、`docs/MUSE_PRODUCT_EVIDENCE_MAP_2026-11-17.md` 及 `docs/PROGRESS_B38{4,6,8,9,90}_*.md`。
- 关键代码目录：`src/`、`pipeline/`、`tts-engines/`、`video-motion/`、`web-portal/`、`ui-prototype/`、`desktop/src-tauri/`、`src-tauri/`、`server/rust/`、`workflows/`、`config/`。

### 2. 绝对工程红线（违反任意一条视为任务失败）：
- **唯一 Commander 权威**：`Muse Commander` 是唯一的规划、权限、预算、TaskQueue/Ticket、审批、取消与验收中枢！**严禁新建平行调度器或绕过 SQLite `TaskQueue`（含 `BEGIN IMMEDIATE` 原子领取、Worker 租约/心跳、fencing、Watchdog）私自起后台野线程跑任务**。Ruflo / dsh / Party 等仅在授权范围内作适配器。
- **严守 AI 视频门禁与 GPU 租约仲裁**：`POST /api/v1/video-products` 直通 `prompt_to_video` 触发的 `HTTP 409 video.commander_admission_required` 是核心安全设计，**绝不能粗暴删掉门禁**！打通视频的方式是把正规链路走通：`前端发起请求 → Commander 创建任务并生成持久化审批单 → 用户在 UI 审批台一键批准（或开启显式用户授权的会话级自动放行策略）→ Video Admission 通过 → VideoTaskWorker 原子领取 → GPU WAL 独占租约仲裁（协调 llama.cpp 让位、实测显存 ≥70% 空闲释放，未知状态 fail-closed）或纯本地素材+Remotion+FFmpeg 路径执行`。
- **诚实分层与事实核验纪律**：
  - 绝不把 mock 夹具、合成正弦波测试音、空壳占位包装成“真实成片”或“真实旁白”。
  - 玄学模块（易经 / `ziwei` / `qimen-rs` / `kinliuren`）所有输出在数据结构和前端 UI 上**必须强制携带 `玄学推演／待验证假设` 标签**，仅作象征观察、跨域类比与可检验假设生成，绝不伪装成科学事实。
  - 并发上限 `32` 受真实 CPU/可用 RAM/Runner/配额与可用 Agent 约束；没有真实在线可用 Agent 时在 UI 如实显示阻塞原因，绝不伪造派发动画欺骗用户。
- **数据安全与隔离纪律**：严守双轨记忆分离（`User Track` 用户事实 vs `Agent Track` 工作经验），SQLite 为唯一权威源，Obsidian Vault 保持单向只读哈希审计镜像；严禁测试污染正式 `data/`（严格使用隔离 workspace）；保持严格 JSON 解析（拒绝重复键）与受限 `exception_type` 失败诊断规范（不泄露裸异常敏感文本）。

---

## 一、 P0 最高优先级：端到端彻底打通「自动创作视频生产线」

把 `pipeline/` + `tts-engines/` + `video-motion/` (Remotion) + `Wan/ComfyUI` + `FFmpeg` + `Product Vault` 串成一条在前端点一下就能丝滑跑完全程的生产级流水线：

1. **补齐从选题到交付的完整自动化闭环**：
   - `选题/灵感输入`（支持直接输入主题、从 Research Wiki 选课题、或从玄学灵感引擎一键转入）
   - `研究求证`：调用现有公开来源研究管线（Bing RSS / SearXNG + Research Wiki 检索），为脚本提供带归因来源与哈希的真实素材支撑。
   - `脚本与分镜规划`：生成结构化分镜单（场景、国语/粤语旁白词、画面生成 Prompt、Remotion 动效数据图层参数、字幕时间轴、画幅与镜头运动）。
   - `Commander 审批与 Video Admission 闭环`：打通持久化审批 API 与前端一键放行交互；清晰区分 **「AI 镜头生成模式（经 Admission + GPU 租约调度本地 Wan/ComfyUI）」** 与 **「纯本地高质感动效模式（Remotion 图形动效 `video-motion/` + 本地视觉资产 + FFmpeg 运镜合成，无需重显存即可 100% 稳定出片）」**，并在 GPU 不可用或未部署 Wan 时透明提示用户可无缝走 Remotion+FFmpeg 高清动效成片路径。
   - `真实旁白封装（tts-engines/）`：打通国语/粤语本地真实 TTS 语音合成与时长对齐、ASR 校验与字幕精确时间轴生成，彻底解决音画同步与听感衔接问题。
   - `FFmpeg 剪辑包装与自动化质检门禁`：完成画幅裁切、转场、防乱码字幕烧录、旁白与配乐混音平衡；运行真实 `ffprobe` 与 QA 质检门禁（检查音视频流、黑帧/静音异常、时长一致性），生成标准 `MP4 + 工程 ZIP + SHA-256 哈希校验 + QA 报告 + manifest.json`。
   - `入库与反馈回链`：自动注册进 Web Portal 的 **Product Vault（产品原型/成片库）** 支持受控流式预览播放与 ZIP 下载；在播放器旁打通 **创作者反馈表单**，提交评分/问题后真实回写 Research Wiki 并通过 `tested_by` 关联回源灵感 `claim`！

---

## 二、 P1 后端全模块“最后一公里”融会贯通

在现有只读 Status API 基础上，扩展完整、受 Commander 权限与 CSRF/鉴权保护的 **交互式控制 API（REST + SSE/WebSocket 实时状态流）**：

1. **玄学灵感引擎正式接入生产工作流**：
   - 将 B384 已安装验证的 `ziwei`、`qimen-rs`、`kinliuren` 及易经推演封装为统一灵感服务接口；
   - 自动将排盘/卦象解析转化为：`象征观察 → 清晰问题 → 可检验假设与替代解释（强制带「玄学推演／待验证假设」标记）→ 写入 Research Wiki (idea / claim 节点)`，支持一键派发给 Commander 启动研究管线或视频生产线。
2. **Commander 任务中心、审批门禁与自进化闭环 API**：
   - 暴露任务提交、DAG 节点状态实时追踪、取消/停止、失败重试、Worker 租约/心跳监控接口；
   - 暴露统一的 **审批中心 API**（Commander 任务授权、Video Admission 放行、Agent Track 经验转正、自我进化改进提案审查批准）；
   - 打通测试/质检失败与复盘数据 → 自动生成改进提案 → 独立 Reviewer 审查门禁 → 用户授权后沉淀经验回写。
3. **Research Wiki、双轨记忆与修订回溯 API**：
   - 为现有的 Research Wiki（`idea/claim/source/experiment/decision/failure` 节点与 `supports/refutes/relates/alternative/tested_by/addresses_gap` 边）提供图谱子图查询、节点详情、证据独立性审查与主动发起研究任务的 API；
   - 提供 `User Track` / `Agent Track` 双轨记忆的分轨浏览、检索、修订历史对比（时光回溯）及 Obsidian 镜像只读哈希校验状态接口；
   - 提供真实硬件与资源仲裁遥测接口（真实 CPU、可用 RAM、存储、网络、GPU WAL 租约持有者与实测显存状态）。

---

## 三、 P2 前端视觉重塑与全界面「真可用」升级（1:1 对标两张设计图）

先梳理 `web-portal/`、`ui-prototype/` 与 `desktop/src-tauri/`（及 `src-tauri/`）的职责关系，收敛到权威的 `web-portal` 前端（并确保 Tauri 桌面端容器加载同一套升级后的前端界面，不破坏桌面打包能力）。

建立统一的 **「东方仙侠宇宙星云 × 敦煌黑金流光 × 赛博像素指令中心」** 视觉设计体系（深空靛紫 `#070412`/`#100A24`、流光黑金 `#F5C563`、灵气青 `#22D3EE`、星芒毛玻璃边框、动态星轨粒子层）：

### 视图 1：Muse 普罗米修斯计划品牌门户（对标图1，路由 `/` 或 `/portal`）
- **顶部导航**：`∞ Muse 普罗米修斯计划 Project Prometheus` + 导航锚点（首页/愿景/能力/架构/成果/路线图/进入控制台）+ 右上角金框铭文「让知识之火不断传递 / 让未知成为探索的起点」。
- **Hero 主视觉区**：主标题、精神标语、右侧竖排书法「观天知命·顺势而为 / 融汇古今·创造未来」、星象八卦动态法阵与能量核心。
- **7节点工作循环光环**：`灵感发现 → 研究求证 → 知识沉淀 → [∞ Muse] → 创作生成 → 验证迭代 → 进化超越`，点击任一节点可联动展示当前仓库真实统计数据并一键跳入 OS 对应工作台。
- **五大核心能力卡片**：AI 多智能体协同、研究知识库 Research Wiki、玄学灵感引擎、视频生产工厂、自我进化系统（全部绑定真实跳转与实时状态徽章）。
- **底部三大真实数据舱**：
  - `真实成果·见证奇迹`：**直接对接 Product Vault 受控流式视频接口**，在线播放真实生成的 MP4 成片并展示 SHA-256 与 QA 徽章（若暂无成片则提供「立即生成首部真实作品」引导按钮，严禁塞假视频占位）；
  - `当前进度·持续进化`：动态汇总真实模块验收状态与健康度圆环；
  - `未来路线图·六大阶段` + 底部跨平台标识（Windows / Linux / 跨平台）与「进入 Super Agent OS」主按钮。

### 视图 2：Muse Super Agent OS 超级智能体调度中心（对标图2，路由 `/os`）
采用一屏掌控的暗黑紫金桌面 OS HUD 框体（顶栏含「超越时空·智启未来」及 Tauri 兼容窗口控制）：
1. **左侧全功能导航栏（8大模块全部做成真可用完整视图，绝无死按钮）**：
   - `总览`：图2 核心主舞台；
   - `任务中心`：TaskQueue 实时列表、DAG 波次可视化、新建任务、取消/重试、**待审批门禁操作台**（一键批准/驳回 Video Admission 与改进提案）；
   - `智能体`：唯一 `Commander` 与专家角色（`Researcher / Architect / Builder / Memory / Tester / Reviewer`）路由白名单、在线/阻塞状态、历史成功率、独立审查通过率与执行日志；
   - `记忆库`：`User Track` 与 `Agent Track` 双轨切换、来源与置信度展示、审批入库、修订历史 Diff 与软删除恢复、Obsidian 镜像哈希审计面板；
   - `知识图谱`：交互式渲染 Research Wiki 真实节点与关系边（支持按 6 类节点染色、按关系过滤、点击查看证据链与 `tested_by` 视频作品回链，并可从真实节点发起研究任务）。
 
---

## B391 分支基线与视频双路径交付补充（2026-11-26）

### A. 不可破坏的 Git 基线

本轮已在本地核验以下事实，后续操作必须持续保护：

- 当前工作分支：`b391-full-fusion`。
- 基线 HEAD：`88250dbfb444b4150cd5587792be829ddb7d7096`。
- 本地 `main` 与该 HEAD 指向同一提交；当时 `HEAD` 与 `main` 均为 44 次提交。
- `1_双击一键验收并保存核心代码.bat` 仍被 Git 跟踪，基线 blob 为 `71c799f04e5546f70144bac67ca0a054dc3e4a78`。
- 开工时工作区已有用户未提交修改及未跟踪产物；这些内容不是垃圾，必须逐项保留。禁止 `reset`、`clean`、`stash`、覆盖 `main`、强制切换分支或擅自提交。任何新改动只落在当前 `b391-full-fusion` 工作区，并先读后写、备份/校验、测试、读回。

### B. 双路径不是二选一，也不能假装等价

**路径 A — 纯本地、低显存、可重复的动效成片**

`主题/脚本/分镜 → 本地真实 TTS（优先粤语）→ ASR/字幕校验 → video-motion/Remotion 动态图形/数据可视化/镜头包装 → FFmpeg 混音、转场、编码 → FFprobe + 全片解码 + QA → MP4 + 工程 ZIP + manifest + SHA-256`。

- 不依赖 Wan/ComfyUI，也不应要求 GPU 大显存；必须有可复现的本地视觉资产或程序化动态图形，不能用空白占位或静态截图冒充动效。
- 把现有 `tts-engines/` 与 Muse 已有本地 TTS 适配器真正接入生产调用链。音频必须来自实际语音引擎和真实模型；模型/运行时缺失时明确阻塞，绝不拿正弦波、静音或测试夹具当旁白。
- 当前 `VideoProductBuilder.build_from_source()` 的 `audio_mode=narration` 是接入用户提供音轨，不等于从旁白文本合成语音；必须补齐文本→TTS→音画时长对齐→ASR/字幕的本地链路。
- Remotion 失败必须使本次产品失败，不允许静默降级成无动效 MP4。记录 Remotion 版本、合成 profile、场景/时间轴与实际渲染证据。

**路径 B — Wan / ComfyUI AI 镜头 + Commander / GPU 安全准入**

`用户创作意图 → Creator Brief/Initiative → 严格 CommanderPlanner 生成并冻结 DAG → TaskQueue 持久化审批 Ticket → 用户真实批准及 human.decision 事件 → 派生 video_pipeline 执行 Ticket → Commander 校验 DAG/审批/哈希并写入 workflow.bound → 专用 VideoTaskWorker.run_approved_ticket → 一次性 Video Admission Grant → GPU WAL 独占租约/心跳/fencing → ComfyUI/Wan 生成 → Remotion/FFmpeg 后期 → 真实 TTS/ASR → QA/完整解码/哈希/独立审查 → Product Vault`。

- `HTTP 409 video.commander_admission_required` 是旧直通入口的正确 fail-closed 行为，绝对禁止删除或绕过。前端必须改走持久化 Commander 审批与专用 Worker，不得继续把 `prompt_to_video` 直接 POST 到旧入口。
- Worker 缺失、DAG 来源不是严格模型规划、审批事件不匹配、授权能力/角色/成本/资源证据缺失、GPU lease 过期或 ComfyUI 不可用时，必须在 GPU 调用前阻断并给出可恢复原因；不能创建假成功任务。
- GPU 与 llama.cpp 共用设备时，只能由现有 GPU Supervisor/Coordinator 协调；必须实测设备显存和心跳，达到配置的释放阈值后才允许恢复模型服务。未知状态 fail-closed，不允许凭环境变量或缓存状态推断空闲。
- AI 镜头完成后仍须经过 Remotion/FFmpeg、真实配音与完整 QA；AI 镜头生成成功不等于最终视频交付成功。

### C. 智能创作入口与降级策略

- 一个统一创作入口根据实际可用能力选择路径：Wan/ComfyUI + Commander Admission + GPU lease 全部 ready 时，提交 AI 镜头审批/执行；否则可明确切换到路径 A 的本地 Remotion + TTS + FFmpeg。
- 切换必须保留用户主题、脚本、旁白、画幅、时长和 QA 要求；必须在 UI 显示实际使用的路径及缺失能力。不得把本地动效包装描述成 Wan AI 镜头，也不得把 409 当作已排队。
- 任何需要人类审批的 AI 执行都必须等待持久化审批事件；不得为了“一键”而自动批准。只有用户明确建立可撤销、范围受限的会话级自动授权策略时，才可按该策略执行。
- 两条路径共享统一产品契约、QA/哈希/ZIP/manifest/Product Vault/反馈回链；路径特有字段必须如实记录。

### D. 必须新增的回归与真实验收

1. API/前端：旧 AI 直通仍返回 409 且零副作用；智能创作改走 Commander Ticket；审批前不触发 Wan；审批后精确绑定同一 Initiative/Brief/DAG/Attempt。
2. Commander/Worker：缺失 Worker、无效/撤销审批、伪造 DAG、过期 Ticket lease、重复派发、Admission grant 重放均 fail-closed；成功必须由 `run_approved_ticket` 和完成证据闭环证明。
3. GPU：竞争租约、心跳丢失、fencing、ComfyUI 未启动、llama.cpp 未确认暂停、显存测量失败均验证不会误派发；不得在单元测试中声称真实 GPU 已验收。
4. 本地动效：真实 Remotion 渲染 + FFmpeg 合成；无 Wan/ComfyUI 时仍可产出本地动效版本；Remotion 失败不得静默降级。
5. 真实旁白：验证真实 TTS WAV、时长、采样率、非静音、ASR CER/字幕时间轴；没有模型时明确失败，不得用 mock 结果冒充成片。
6. 成片：`ffprobe` 流信息、完整解码、黑帧/冻结帧/静音/响度/字幕检查、ZIP 内容与 SHA-256 全部通过后才可标记技术 QA passed；仍需人工内容/版权审片才可发布。
7. 设备验收与 mock 测试分开记录。没有真实 Windows GPU/ComfyUI/模型/TTS 运行时的证据时，状态必须标记为“代码/模拟测试通过，实机待验收”，绝不承诺无条件 100% 成功。

### E. 本轮已观察到的具体断点（不可误报为已修复）

- `ui-prototype/src/App.svelte:createVideoProduct()` 当前 AI 分支仍调用 `client.createVideoProduct({mode: "prompt_to_video", ...})`；`ui-prototype/api.js` 将其直接 POST 到 `/api/v1/video-products`，因此触发 409。该界面还把 ComfyUI 未 ready 设为按钮禁用条件，没有可用的本地自动/明确切换策略。
- `status_api.py` 的旧 API 对 AI 请求返回 409；本地素材路径则启动旧的单线程渲染工作，不等于 Commander AI 执行链。
- `VideoTaskWorker.run_once()` 已明确是 deprecated/blocked entrypoint；专用入口是 `run_approved_ticket()`。但 `AutonomousSupervisor._video_worker()` 仍调用 `run_once()`，现有测试也在断言这个旧行为，必须修正调用/测试而不是撤销 Worker 安全边界。
- `MuseRuntime.dispatch_video_pipeline_once()` 要求注入实现 `run_approved_ticket` 的专用 Worker；缺失 Worker 会让执行 Ticket 失败并记录 `execution_started=false`。必须打通真实 Worker 注入与 Ticket 选择/恢复。
- `build_from_source()` 当前只接收已有素材和可选音频文件；它没有从脚本调用本地 TTS 的参数/流程。现有 `build_from_prompt()` 已具备可选粤语 TTS/ASR 封装，但它属于 Wan 路径，不能证明本地素材路径已具备真实旁白。
- 当前 Remotion README 描述的集成范围主要是品牌片头/片尾和逐镜转场；必须以实际 Remotion 渲染产物和时间轴证明动效，而非只看 UI 选项或 README。

**最终完成定义：**两条路径分别通过独立测试和至少一条真实本机样片验收；路径选择、准入、语音、动效、QA、交付和失败恢复都有可追溯证据。未完成任何一项都只能报告部分完成/阻塞，不能宣称“智能创作 100% 稳定交付”。