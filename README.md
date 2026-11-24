# Muse — 把灵感编译为真实创造的本地优先智能体平台

> **让知识之火不断传递，让未知成为探索的起点。**
>
> 状态快照：**2026-11-24（B390）** · 持续开发中 · 本介绍按「已验证 / 已实现待验收 / 开发中 / 规划中」诚实分层，不把路线图包装成现成功能。

Muse 是一个由创造者发起的长期智能体项目：以唯一的 **Muse Commander** 为调度中枢，把长期记忆、研究求证、专家协作、内容创作与验证改进连接进同一条可追溯、可审计的工作循环。它不是单轮聊天助手，也不是要堆叠一群聊天机器人；"意识体""宇宙最强"等表述仅作品牌修辞与长期愿景，**不代表软件已被证明拥有主观意识或客观排名**。

---

## 一、产品宪章：五大支柱

完整宪章见 [`docs/VISION.md`](docs/VISION.md) 与 [`docs/MUSE_CONSTITUTION.md`](docs/MUSE_CONSTITUTION.md)，精神纲领见 [`docs/PROJECT_PROMETHEUS.md`](docs/PROJECT_PROMETHEUS.md)。

1. **灵感与记忆根基** — 从已批准记忆、用户目标与跨领域知识提出假设；易经、紫微、奇门、大六壬等传统术数只作文化象征与灵感生成，输出一律标记为「玄学推演／待验证假设」，绝不伪装成事实、科学证据或确定预测。不把 Agent 推测自动写成用户事实。
2. **超融合研究与代码进化** — 研究本地授权项目、官方文档、GitHub、论文与社区，保存 URL、日期、版本、license、内容哈希与可信度限制；按职责复用/适配 Ruflo（Agent/Swarm）、dsh（Ticket/授权/恢复）、Super Agent Party、agent-desktop，不复制重复控制平面。
3. **唯一 Commander 与专家调度** — Commander 是唯一权限与总调度权威；Ruflo、dsh、Party 等只能在授权范围内作适配器，不形成第二套队列或调度中心。选择执行者依据能力、在线状态、历史成功率、独立审查通过率、成本与权限匹配；没有真实可用 Agent 时明确阻塞，不伪造派发。
4. **资源感知并行引擎** — 并发受真实 CPU、可用 RAM、Runner 上限与 Provider 配额共同约束；保留系统余量，硬上限、背压、超时、取消、失败隔离、有限重试不可省略。GPU/显存只有实测验证后才参与调度，未知即标记 unknown，不超卖。
5. **万象超融合与灵感变现实** — 任何学科、文化、艺术、工程与个人灵感都可作为入口；建立「灵感捕获 → 解构 → 跨域组合 → 方案与任务图 → 专业工具编排 → 原型与作品 → 测试反馈 → 知识沉淀」闭环，创造目标不限软件，含视频、影视、艺术、音乐、游戏与虚拟世界。

工作循环：观察 → 找缺口 → 研究 → 提案 → 多 Agent 分工 → 实现 → 测试 → 独立审查 → 授权整合 → 经验回写 → 再检查。循环必须由真实任务、证据和验收驱动，不空转、不虚报。

---

## 二、当前状态速览（2026-11-24 / B390）

| 成熟度 | 能力 | 证据 |
|---|---|---|
| **已验证** | 本地任务队列、Worker 租约/心跳、崩溃回收、Watchdog；SQLite 双轨记忆与修订历史；受约束 DAG 规划、专家路由、独立审查门禁；公开来源研究管线与 Research Wiki 图谱；Web Portal 登录态/只读状态 API；Linux x86_64 原生构建与 Debian 包 | 隔离 Linux 副本全仓 **2,475 tests / 0 failures / 0 errors**；Node **66/66**；Rust `cargo test` **15 passed**，`clippy -D warnings` 通过；Debian 包 smoke test 通过。详见 [`docs/PROGRESS_B384_LINUX_NATIVE_CROSS_PLATFORM.md`](docs/PROGRESS_B384_LINUX_NATIVE_CROSS_PLATFORM.md) |
| **已实现待完整验收** | 粤语本地 TTS/ASR 与旁白封装；视频产品打包与 ZIP 哈希校验；GPU 资源租约与 Supervisor 让位；Web Portal 离线产品原型库（预览/下载受控代理）；创作者反馈回写 Research Wiki | 有真实本地合成/测试封装记录与专项回归，但人工听感、真实 Wan 镜头旁白、端到端发布门禁仍未完成。详见 [`docs/MUSE_PRODUCT_EVIDENCE_MAP_2026-11-17.md`](docs/MUSE_PRODUCT_EVIDENCE_MAP_2026-11-17.md)、[`docs/PROGRESS_B387_PORTAL_PRODUCT_VAULT.md`](docs/PROGRESS_B387_PORTAL_PRODUCT_VAULT.md)、[`docs/PROGRESS_B135_GPU_RESOURCE_ARBITRATION.md`](docs/PROGRESS_B135_GPU_RESOURCE_ARBITRATION.md) |
| **开发中（本轮 B386–B390）** | TaskQueue 锁等待后的租约时钟重采样；自主运行状态语义修复（停止不再误报 stale）；失败诊断只记录受限 `exception_type`、不落盘异常消息；严格 JSON 解析（拒绝重复键）；产品反馈回链到源灵感 claim（`tested_by`）；插件执行沙箱审计（默认 fail-closed） | B390 专项：Worker 40/40、失败分类 149/149、自主运行+Status API+Portal 168/168、Portal 19/19；反馈import 6/6、跨管线 38/38。详见 `docs/PROGRESS_B38{6,7,8,9}_*.md` 与 `docs/PROGRESS_B390_*.md` |
| **规划中** | 经用户授权的 Windows 常驻服务与无人值守；多平台 OAuth/API 发布、数据回流与收益复盘；Android APK/AAB、iOS/macOS 构建与真机 PWA 验收；动态插件执行（WASM 隔离 + 能力代理 + 资源配额 + 审批 + 回滚）；公网部署与反向代理安全验收 | 尚无对应实现/授权，不作已可用宣传。Android 仅完成只读 preflight（报告 JDK/SDK/Rust target 缺失，未构建） |

> 统计口径：2,475 为 B385 隔离副本全量基线；B386–B390 多为**专项回归**而非重跑全量，逐项范围以对应进度文档为准。正式 `data/` 曾因早期并行测试留下历史写入痕迹，已做 workspace 隔离修复，历史记录未被删除或回滚，后续只做只读取证。

---

## 三、核心能力地图

### 1. Commander、任务与专家协作
- **Muse Commander**：统一目标、权限、预算、TaskQueue/Ticket、审批、停止/取消与最终验收；不新增平行调度器。
- **任务生命周期**：SQLite 持久化队列、租约与心跳、`BEGIN IMMEDIATE` 原子领取、过期回收、有限重试、fencing 防止旧 Worker 完成已被接管的任务。
- **专家路由与 DAG**：角色 allowlist、依赖校验、safe fallback、并行 wave；32 是架构硬上限，**不代表 32 个真实 Agent 常驻在线或已跑满 32 路推理**。
- **独立审查门禁**：Reviewer 与 Builder 身份分离；高危发现可否决，但审查器不是自动正确性证明。
- 相关文档：[`docs/AGENT_COMMANDER.md`](docs/AGENT_COMMANDER.md)、[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

### 2. 记忆、研究与 Research Wiki
- **双轨记忆**：User Track（用户事实）与 Agent Track（工作经验）分存；内容、来源、时间、版本、置信度、审批状态齐备；修订历史与软删除可追溯。
- **Obsidian 镜像**：SQLite 为权威源，Obsidian Vault 为**单向、只读哈希审计**的 Markdown 镜像，不双向同步。
- **研究管线**：多角度搜索（Bing RSS 无需 key、SearXNG 可配置）、来源抓取与归因、内容哈希、主张拆分、支持/反证/替代解释。
- **Research Wiki**：`idea / claim / source / experiment / decision / failure` 节点，配 `supports / refutes / relates / alternative / tested_by / addresses_gap` 等关系与证据审查数据模型。当前快照有规模积累（约 910 活跃节点、2,459 边、281 条证据候选），但 **证据审查与来源独立性数据仍不足**——这是「已有机制」，不是「所有主张均已独立审查」。
- 只读审计工具：`muse-research-wiki-audit`，`mode=ro` + `query_only`，不写知识库。

### 3. 灵感、玄学与超融合
科学、工程、艺术、文化、神话与个人灵感都可触发问题；易经、紫微斗数、奇门遁甲、大六壬等只用于象征观察、类比与假设生成，**不是科学证据**。每个灵感尽量转化为：清晰问题 → 可检验假设与替代解释 → 研究/实验计划 → 可体验原型 → 含成功/失败/局限的复盘。玄学灵感引擎（ziwei / qimen-rs / kinliuren）已做隔离依赖安装与版本核验（B384），但**尚未全部接入生产工作流**。

### 4. 视频生产线（真实链路，非低质批量生成）
`选题 → 研究 → 脚本/分镜 → 素材 → 视频生成 → 剪辑包装 → 质量检查 → 产品交付 → 发布复盘`

- 本地 Wan / ComfyUI 生成镜头，FFmpeg 完成画幅/转场/字幕/音轨，Remotion 负责动效图形，输出 MP4 + 工程 ZIP + QA 报告 + manifest。
- **真实试产记录（2026-11）**：一条 24.5 秒、9:16、1080×1920、24 FPS 的粤语字幕短片《AI 讲得咁肯定，就一定啱？｜三步查证法》，技术 QA 与 ZIP 完整性通过。该片**复用此前生成的 Wan 2.1 镜头**，本轮未重新推理；**无旁白、仍处 testing、未发布、无收入数据**。它证明的是一条真实的本地视频合成与交付路径，不是自动商业发布。
- **AI 视频审批门禁**：`POST /api/v1/video-products` 的 `prompt_to_video` 直通 Wan 路径已 fail-closed（HTTP 409 `video.commander_admission_required`）；AI 视频必须经持久化 Commander 审批与 Video Admission 后由 `VideoTaskWorker` 执行，纯本地素材 + FFmpeg 路径保留。
- GPU 资源租约：Wan 生成与 llama.cpp 模型服务共享 AMD GPU 时，通过 SQLite WAL 独占租约、心跳续租、TTL 回收、实测显存 ≥70% 空闲才确认释放；未知状态 fail-closed，不猜测显存。
- 相关文档：[`docs/MUSE_HIGH_VALUE_VIDEO_FACTORY.md`](docs/MUSE_HIGH_VALUE_VIDEO_FACTORY.md)、[`docs/COMMERCIAL_VIDEO_PILOT_20261114.md`](docs/COMMERCIAL_VIDEO_PILOT_20261114.md)

### 5. 桌面、Web Portal 与平台
- **桌面壳**：Tauri 2 + Rust（`desktop/src-tauri/`），启用 `custom-protocol` 后用内嵌生产前端，不依赖 Vite 端口。
- **本地控制台**：loopback-only 浏览器宿主与 Status API v1（Bearer 认证），只读展示 Agent、DAG、任务、审查、知识与真实事件时间线；无连接时明确显示空态/错误。
- **Web Portal 产品库（B387）**：灵感/Commander TaskQueue/演化 Worker/Initiative → 离线产品原型，Portal 只展示 `offline_product`，对 Initiative ID、版本 ID、预览/下载路径做严格校验与 HTML 转义；固定 allowlist 只读代理、服务端 Bearer、禁用代理与重定向、仅 HTML/ZIP MIME、20 MiB 上限、严格 CSP + sandbox，缺失策略即 fail-closed。产品创建成功后轮询 Commander 账本自动刷新。
- **反馈闭环（B388–B390）**：离线产品导出的 JSON 由本地 CLI 显式导入，严格 schema、1 MiB 上限、拒绝重复键、校验产物哈希、幂等内容摘要；创作者观察作为**未验证** `experiment` 节点写入 Research Wiki，并以 `tested_by` 回链到源灵感 claim——记录测试关系，不等于支持/反驳/升级主张。
- **跨平台**：Linux x86_64 原生工作副本 + Debian 包（`artifacts/linux/Muse_0.1.0_amd64.deb`，SHA-256 `cd2b20ce…`，见 B384）；Android 仅只读 preflight，iOS/macOS 未构建。

### 6. 安全、审计与自我进化
- 本地处理私人记忆；未经明确同意不上云；不接付费 API/fallback，额度未知即 fail-closed。
- 改进闭环：研究提案 → 来源/授权审查 → 能力缺口 → Ticket → 专家执行 → 测试 → 独立 Reviewer → 人工批准/整合 → 经验回写；提案者不得自批。
- 失败诊断只持久化受限 `exception_type`（ASCII 标识符、≤80 字符），**不记录异常 message、traceback、payload、路径或 token**（B390）。
- 插件执行默认关闭；当前登记/签名只是准入审计，`execution_enabled=false`。真实执行需补齐 WASM 隔离、能力代理、资源配额、Commander 一次性授权、审计撤销与 kill switch（[`docs/PLUGIN_SANDBOX_EXECUTION_AUDIT_B387.md`](docs/PLUGIN_SANDBOX_EXECUTION_AUDIT_B387.md)）。
- 高风险、不可逆、对外发送、付款、删除或私人资料外传必须明确批准；系统需有停止/暂停、资源上限、审计、恢复与回滚。

---

## 四、环境要求与快速开始

**环境**：Python 3.11+（核心仅用标准库；部分研究/语音/测试依赖按模块可选）；Node.js（Web Portal / UI）；Rust + Tauri（桌面壳）。无需安装全局依赖即可运行核心 CLI。

```powershell
# 核心自检
$env:PYTHONPATH = 'src'
py -3.14 -m unittest discover -s tests -v
py -3.14 -m muse.main --check

# 本地模型与记忆
py -3.14 -m muse.chat_cli "你好，Muse" --thread main
py -3.14 -m muse.memory_cli add "偏好简洁回复" --project profile --source confirmed --kind preference --track user

# 灵感 → 研究 → 自主规划
py -3.11 -m muse.inspiration "为 Muse 设计下一阶段突破方向" --project muse
py -3.11 -m muse.research "Muse autonomous agent frameworks"
py -3.11 -m muse.autonomy --loop --interval-seconds 1800
py -3.11 -m muse.autonomy --list

# 任务队列与 Worker（等待人工批准）
py -3.11 -m muse.worker --db data/tasks/muse.sqlite3
py -3.11 -m muse.task_cli --db data/tasks/muse.sqlite3 approve TASK_ID --by local-user
py -3.11 -m muse.worker --auto-workers --max-workers 32   # 硬上限，默认单并发

# Commander DAG（需显式批准与已配置 Provider）
py -3.11 -m muse.unified_runtime commander-once --task-id TICKET_ID --approve --max-workers 4

# 研究 Wiki 只读审计 / 产品反馈导入
py -3.11 -m muse.research-wiki-audit
py -3.11 -m muse.research-wiki-audit --limit 3
py -3.11 scripts/import_product_feedback.py --workspace . --file .\muse-inspiration-lab-<initiative-id>.json

# 本机状态 API 与桌面
py -3.11 -m muse.status_api --task-db data/tasks/muse.sqlite3 --progress-db data/progress/muse.sqlite3 --port 0
scripts\Muse-Desktop.ps1
```

**日常 Windows 使用（无需 ChatGPT）**：运行 `scripts\Install-Muse-Shortcut.ps1` 安装快捷方式，双击打开本机 App 窗口；停止用 `scripts\Muse-Stop.ps1`。主电脑访问 `http://127.0.0.1:8766/`；其他设备同 Wi-Fi 连接需首次配对码（每次重启轮换，授权 12 小时）。共享服务只绑私有网络地址，**不要在公共 Wi-Fi 或端口转发环境使用**，HTTP 局域网流量未加密。

**一键视频合成（已有素材）**：
```powershell
scripts\muse-video.cmd make "D:\Media\素材" "D:\Media\成片.mp4" --audio "D:\Media\bgm.mp3" --audio-mode music --size 1920x1080
scripts\muse-video.cmd engines   # 只读探测 ComfyUI / Velorn MCP，不启动服务
```
支持图片/视频混合、循环 BGM 或旁白补静音、AMF/软件编码、FFprobe 校验；不覆盖已有输出，不修改源素材。

---

## 五、验证证据与复现口径

- **全量基线（B385，隔离 Linux 副本）**：Python 2,475 passed / 0 failures / 0 errors / 59 skipped，`compileall` 通过；Node 类型检查 + 66/66 tests + Vite 构建通过；Rust fmt/test/clippy/check 通过，Debian 包 6,432,880 bytes，SHA-256 `cd2b20cea19411798993191e9b4281e0d15b42c6d35b53e9250ce384b7ef6be1`，包内 Status API smoke test（loopback / Bearer / health）通过。
- **B390 专项**：Worker 40/40（canonical Windows 副本）、失败分类 149/149、自主运行 + Research + Status API + Portal 168/168、Portal 19/19、产品反馈 import 6/6、跨管线 38/38；Python `py_compile` 与 `node --check` 通过。
- **事实核验底稿**：[`docs/MUSE_PRODUCT_EVIDENCE_MAP_2026-11-17.md`](docs/MUSE_PRODUCT_EVIDENCE_MAP_2026-11-17.md) 记录两批目标测试（8 文件 140 项 + 7 文件扩展）与 Research Wiki 只读快照；运行环境为项目 `.venv` Python 3.11.9，标准库 `unittest`；**环境无 pytest，不宣称 pytest 验证通过**。
- **口径纪律**：代码存在 ≠ 端到端验收；测试存在 ≠ 本轮已执行；测试夹具/合成测试音 ≠ 真实作品或真实旁白。统计均标注范围与日期，不跨基线混算。

---

## 六、目录导览

| 路径 | 用途 |
|---|---|
| `src/muse/` | 核心运行时：Commander、任务队列、记忆、研究、审查、视频、状态 API 等 |
| `tests/` | unittest 回归（全仓 2,475 项隔离基线） |
| `scripts/` | 启动/停止、桌面构建、GitHub 同步、视频、反馈导入、Linux/Android 预检等 |
| `docs/` | 产品宪章、架构、路线图、逐批进度与事实核验底稿；从 [`docs/PROGRESS.md`](docs/PROGRESS.md) 起步 |
| `web-portal/` | 本机浏览器控制台（状态、灵感、原型库、反馈） |
| `desktop/src-tauri/` | Tauri 2 + Rust 桌面壳源码 |
| `ui-prototype/` | 真实 API 驱动的只读桌面 UI 原型 |
| `config/` | 非敏感配置样例（真实密钥/配置不入库） |
| `deploy/`、`tools/` | 部署与本地工具脚本 |

运行态目录 `data/`、`artifacts/`、`models/`、`vault/`、`.venv/`、`node_modules/`、`target/` 等**不纳入版本控制**。

---

## 七、原则与边界

1. 主动触达必须由明确配置的定时/事件触发器完成，不假设模型会在后台自行思考。
2. 记忆可查看、可导出、可修订；敏感资料默认不收集，私人资料默认本地保存。
3. 电脑操作按最小权限执行；删除、覆盖、付款、发送消息、部署、外部发布须独立授权。
4. 每项自动化都要有日志、结果状态和失败说明；不把模型推测写成用户事实。
5. 玄学内容只作灵感与假设；证据用于校准结论与落地风险，不作为灵感的准入门槛。
6. 未来愿景必须与当前已实现功能明确分开，不把概念演示包装成已上线能力。

详细运行手册见 [`docs/WEBCODEX_INTERFACE.md`](docs/WEBCODEX_INTERFACE.md)，Git 工作流见 [`docs/GIT_WORKFLOW.md`](docs/GIT_WORKFLOW.md)，功能逐项交付规范见 [`docs/MUSE_FEATURE_DELIVERY_DOCTRINE.md`](docs/MUSE_FEATURE_DELIVERY_DOCTRINE.md)。
