# Muse Super Agent OS — Desktop UI Product Specification

更新：2026-11-02
最新增量（2026-11-14 / B-127）：任务中心已加入本地任务名称/编号搜索、状态筛选、已载入数量和截断提示；`npm test` 29/29、`npm run check` 0 errors / 0 warnings、Vite build 117 modules 通过。隔离 Cargo target 的 offline Tauri Release build 也通过：`desktop/src-tauri/target/b127-client-release-check/release/muse-desktop.exe`，8,686,592 bytes，SHA-256 `9527524ec17f170b9cd7263b3883d2514e473b108a74b38de79b44260b91f2a4`。新 EXE 尚未启动，桌面窗口人工验收仍待完成。详细记录见 `docs/PROGRESS_B127_CLIENT.md`。
状态（2026-11-14）：Tauri 2 + Rust 桌面壳、Svelte 5/TypeScript 控制台、loopback Python API 与窄 IPC 桥接已建立。B-125 前端复核：Svelte 检查 0 errors / 0 warnings、Node UI 测试 25/25、Vite production build 通过；Python 3.11 全量 unittest 1,164/1,164 通过。B-125 新 UI 已通过独立 Cargo target 的 offline Tauri Release build：desktop/src-tauri/target/b125-release-check/release/muse-desktop.exe，8,686,080 bytes，SHA-256 1d56b720f97bccaf37e532c2daaf4a3b2676b6dd9f33be63a952bf180b94b6fb。现有桌面实例未被替换，新 EXE 尚未启动；人工桌面交互/截图验收仍待完成。MSI/NSIS 安装包、托盘和通知仍未完成。详细记录见 docs/PROGRESS_B126_VERIFICATION.md。
## 1. 产品定位

Muse 不应只是聊天窗口，而应成为本地优先、可审计、可授权的 Super Agent 调度中枢。使用者能够观察 Agent、任务、工具、知识、审批和系统健康状态，并能以直观操作发起、暂停、批准、取消和追踪工作。
产品原则：
- 真实状态优先：所有数字、进度、在线状态和能力状态必须来自后端 API/事件，不得用静态演示数据伪装运行状态。
- 操作可解释：高影响操作显示作用范围、风险、所需权限和确认步骤。
- 本地优先：默认连接本机 Muse；未明确授权不得把私人记忆、文件或任务上下文发送到云端。
- 渐进增强：先有可用的任务/Agent 控制台，再逐步增加视觉特效；动画不能妨碍可读性和性能。
- 后端为事实来源：前端不直接读写 SQLite，不绕过 TaskQueue、Mandate、审批和角色权限。

## 2. 视觉方向：Cyber-Mythic Pixel

关键词：赛博复古、像素艺术、中国古典建筑、星象、山海经神兽、符箓/阵法、东方神话、霓虹科技。

视觉语言：
- 基础底色：墨黑、夜蓝、深紫；信息层级以青蓝/玉色表达正常状态，琥珀金表达重要/待批准，朱红表达失败/危险。
- 装饰元素：像素云海、飞檐、星轨、符纹、铜金边框、神兽剪影、灵核/阵眼。
- 核心视觉：中央「Muse 灵枢/调度阵」展示 Agent 节点与任务依赖连线；节点状态必须映射真实 Agent 状态。
- 动效：轻量扫描线、呼吸光、粒子/星尘、节点连线流动；支持减少动态效果；低性能设备可切换静态模式。
- 字体：中文正文优先清晰易读的系统字体；像素/书法字体只用于标题、徽章和少量装饰，不用于密集数据。
- 像素元素采用统一网格与边缘规则，避免把每个面板做成过度复杂的游戏 HUD。

## 3. 信息架构与主要页面

1. 总览 / 灵枢：系统健康、活跃 Agent、队列、最近事件、待审批数量、资源概览、关键操作入口。
2. 任务中心：任务列表/看板、优先级、状态、依赖 DAG、执行日志、重试/取消/恢复（遵循后端策略）。
3. Agent 群像：角色目录、能力声明、当前工作、模型/provider、健康状态、权限摘要；角色权限编辑需要审批与审计。
4. Swarm 编排：可视化 DAG 编辑/预览、依赖校验、并行波次、阻塞链路、每个 Agent 的输入输出交接。
5. 审批与审查：待批准工作、能力请求、风险说明、Reviewer 独立审查结果、测试证据和回滚方案。
6. 记忆与知识：User/Agent 双轨、类型筛选、来源与修订历史、候选审批、Obsidian 镜像审计；不得混淆两条记忆轨道。
7. 工具与资源：本机工具/MCP/provider 连接状态、能力就绪/需批准/禁用状态、配额与健康探测。
8. 事件时间线：任务、Agent、研究、测试、审查、系统事件的实时/历史流。
9. 设置：连接地址、主题/动画强度、隐私选项、日志和诊断导出；敏感设置必须显式确认。

## 4. 技术架构建议

推荐桌面壳采用 Tauri 2 + Rust，视觉层采用 Svelte + TypeScript（React 可作为备选，但第一版只选一种）。Muse 现有 Python 服务继续作为业务核心。不要为 UI 重写现有后端。

职责边界：
- Rust/Tauri：桌面窗口、系统托盘/通知（后续）、安全的本机服务生命周期桥接、文件选择对话框、打包与更新基础设施。Rust 命令只暴露窄接口，不直接拥有业务数据库。
- Svelte/TypeScript：页面、交互、可视化 DAG、动画、主题和可访问性。
- Python Muse：MemoryStore、TaskQueue、Worker、Orchestration、RoleRegistry、Approval、ProgressEventStore、Provider/Tool adapters。
- API 层：先定义版本化 localhost HTTP + WebSocket/SSE 事件协议；认证使用本机随机会话凭据/受限 loopback 绑定，校验 Origin，避免开放到局域网；后续评估 Tauri sidecar 或嵌入式服务管理。

Rust 不等于自动安全：仍需最小权限、输入验证、来源校验、进程隔离、权限审批和安全更新。任意 Python AgentCall 仍不能因套上 Tauri 就变成沙箱。

## 5. 前后端并行交付路线

### UI-0：产品与接口契约（现在）
- 完成视觉规范、页面信息架构、交互原则和 API/事件数据模型草案。
- 盘点现有 ProgressEventStore、TaskQueue、OrchestrationStore、CapabilityRegistry、Approval 数据，确定每个 UI 指标的真实来源。
- 输出静态高保真页面原型；所有示例数据标记为 mock，不连接真实操作。

### UI-1：只读控制台 MVP
- 建立 Tauri 2 + Rust + Svelte/TypeScript 客户端骨架（安装依赖前确认 D 盘目标路径与工具链）。
- Python 侧增加只读状态 API：health、queue summary、agents/runs、recent events、capability status。
- UI 显示总览、任务列表、Agent 状态、事件时间线；断线/过期数据有明显提示。
- 安全验收：只绑定 127.0.0.1；随机会话凭据；Origin 校验；无写操作 API；不输出敏感 payload/密钥。

### UI-2：受控操作与审批
- 通过后端命令接口执行任务创建、取消、批准、拒绝和恢复；每个命令检查角色、能力、Mandate 与审批。
- 前端展示操作预览、影响范围、确认提示和审计结果。
- 不允许前端直接调用 shell、任意路径写入或直接 SQL。

### UI-3：实时 Swarm 可视化
- 后续通过 WebSocket/SSE 推送结构化事件；当前 MVP 使用 5 秒有界轮询并显示 stale 状态，不能宣称为 push stream；显示 DAG 波次、交接、阻塞和失败原因。
- 支持筛选、时间回放、任务详情与 Reviewer 证据。
- 大规模节点采用虚拟化/分层渲染，降低动画负担。

### UI-4：主题与产品化
- 像素素材包、神话主题皮肤、音效（默认静音）、动效强度、减少动态模式、键盘操作与高对比度模式。
- Windows 安装包、升级/回滚、崩溃日志脱敏与诊断导出。

## 6. 第一版验收标准

- 用户打开客户端能在 5 秒内看清系统是否在线、多少任务运行、多少 Agent 活跃、哪些事项待审批。
- 总览中的状态来自 API，断网时显示最后更新时间并标记 stale，不得伪装实时。
- 能从任务卡进入 DAG/日志详情，理解任务为什么运行、阻塞或失败。
- 只读 MVP 不含危险写操作；后续写操作全部经后端授权和审计。
- 1080p 窗口可读，窗口缩放正常；减少动态模式可用；界面不因像素装饰牺牲文字对比度。
- 本地数据默认不离开设备；客户端不要求先迁移现有 SQLite 数据。

## 7. 当前明确不做

- 不把 UI 假数据当成真实 Agent 运行情况。
- 不在第一版就实现远程公网控制、开放 LAN 端口或云端控制平面。
- 不因追求 Rust 而重写 Python 业务逻辑。
- 不自动安装未知 UI 模板、插件或第三方二进制。
- 不在用户未批准前安装 Rust 工具链、Node 依赖、Windows 服务或开机自启组件。
