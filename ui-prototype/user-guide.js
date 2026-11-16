export const USER_GUIDE_STEPS = [
  {
    id: "overview",
    title: "意识中枢 · 先看系统是否健康",
    purpose: "这里是 Muse 的驾驶舱。它把本地 API 连接、智能体数量、任务与近期活动放在同一处。",
    howToUse: "打开 Muse 后先看顶部更新时间与核心连接状态，再按「刷新状态」更新数据。若出现「数据已过期」，先检查本机 Muse 后端是否仍在运行。",
    result: "你会看到来自本地 API 的真实状态；没有数据时会明确显示未连接或空状态，不会用演示数字冒充在线。",
    boundary: "总览目前以监控为主，不是所有任务都能在这里直接启动或批准。",
    target: "#overview",
    action: "查看意识中枢",
    availability: "已实现 · 真实状态"
  },
  {
    id: "inspiration",
    title: "灵感工坊 · 把想法交给 Muse",
    purpose: "保存灵感、问题、故事种子或产品构想，形成可追踪的创作入口。",
    howToUse: "在「写下你的灵感」输入内容（最多 2000 字），然后点击「保存灵感并交给 Muse」。默认不勾选联网研究，内容只保存到本机 Research Wiki。",
    result: "灵感会出现在左侧历史列表。你可以看到保存时间、研究许可与当前状态。",
    boundary: "只保存灵感不代表它已经完成研究，也不代表已自动生成软件或影片。",
    target: "#inspirations",
    action: "提交一条灵感",
    availability: "已实现 · 本地保存"
  },
  {
    id: "research-privacy",
    title: "研究许可 · 由你决定是否联网",
    purpose: "把本地记录与公开网络研究分开，避免把隐私内容默认发送到外部。",
    howToUse: "只有当你明确希望 Muse 用这条灵感搜索公开资料时，才勾选「允许 Muse 使用这段灵感进行联网研究」。确认后再保存。",
    result: "该灵感会带有联网研究许可并进入自主研究候选池；来源与研究结果按 Muse 的知识账本流程保存。",
    boundary: "勾选只表示允许进入研究流程，不保证当前网络、搜索服务或模型一定可用；玄学内容只作灵感假设，不作事实证据。",
    target: "#inspirations",
    action: "检查研究许可选项",
    availability: "已实现 · 明确授权"
  },
  {
    id: "video",
    title: "视频创作 · 两种本地制作方式",
    purpose: "使用本机 Wan 2.1 / ComfyUI 生成短片预览，或用 FFmpeg 把已有素材剪辑合成 MP4。",
    howToUse: "选择制作方式并填写影片名称。AI 模式填写画面描述、可选负面提示词、画幅与 1–5 秒预览时长；确认 ComfyUI 显示就绪后点「一键 AI 生成 MP4」。素材模式先把图片/视频放进 data/video_sources/你的素材文件夹，再填写文件夹名及可选音轨。",
    result: "任务状态会写入持久化账本。成功后产品进入成果库，可预览、下载 MP4 与工程 ZIP。",
    boundary: "AI 生成需要本机 ComfyUI 与 Wan 模型实际可用；短片是预览级输出，不能把引擎检测就绪当成成片验收。GPU 任务失败后应先检查任务记录，不会自动重复消耗显卡资源。",
    target: "#products",
    action: "打开视频创作",
    availability: "部分实现 · 本机依赖"
  },
  {
    id: "products",
    title: "成果产品库 · 预览、下载、审片",
    purpose: "集中查看 Muse 已经生成并登记的产品、视频、工程包和创作履历。",
    howToUse: "在产品条目点击「预览」查看作品；点击「下载 MP4」取得视频，点击「下载工程 ZIP」保存工程包。点「创作时间线」查看版本、失败、知识关联等事件；支持反馈的作品可填写审片意见并选择通过、修改或拒绝。",
    result: "审片反馈会回到 Creator Initiative，供后续研究和能力改进使用。",
    boundary: "作品初始状态可能仍是 testing；只有实际检查内容并提交验收意见，才代表人工审片完成。",
    target: "#products",
    action: "查看成果产品库",
    availability: "已实现 · 交付与审片"
  },
  {
    id: "agents",
    title: "智能体舰队 · 看清谁在线、擅长什么",
    purpose: "查看已登记 Agent 的角色、能力声明、心跳和真实效果样本。",
    howToUse: "找到「天枢 · 智能体阵列」，检查在线、执行中、离线状态及最近心跳；任务成功率和 Reviewer 通过率只在有真实样本时显示。",
    result: "你可以判断本地 Agent Registry 是否配置，以及有哪些已登记 Agent 可供后端调度。",
    boundary: "这个页面目前是状态监控，不等于已连接所有外部 Agent，也不能从这里直接命令任意 Agent 执行。",
    target: "#agents",
    action: "查看智能体舰队",
    availability: "已实现 · 状态监控"
  },
  {
    id: "queue",
    title: "任务队列 · 观察 Commander 的工作",
    purpose: "查看排队、执行、待审批、失败任务，以及可观测的 Commander 工作流和 Worker 心跳。",
    howToUse: "检查四类任务计数，再看活跃任务的状态、依赖数量、尝试次数与更新时间。若有流程卡片，可查看当前阶段、DAG 步骤和下一步提示。",
    result: "你能知道工作停在哪个阶段、是否需要审批、Worker 心跳是否过期。",
    boundary: "当前桌面队列是只读监控面板；批准、取消、重试和恢复不是这里已开放的按钮。不要把「显示待审批」误解为「可以在此批准」。",
    target: "#queue",
    action: "查看任务队列",
    availability: "已实现 · 只读监控"
  },
  {
    id: "evolution",
    title: "进化实验室 · 把问题变成改进提案",
    purpose: "记录结构化反馈，运行能力雷达，查看研究来源、验收标准与回滚方案。",
    howToUse: "选择反馈类别、问题标签和 1–5 严重程度，点「记录反馈」。需要扫描时点「扫描改进机会」；再点提案查看问题、假设、影响、来源、验收标准与回滚计划。",
    result: "反馈和提案会进入持久化改进账本；提案可被后续研究补充证据。",
    boundary: "扫描不是自动升级。提案仍需要来源/授权审查与人工批准；系统不会因你记录一次反馈就擅自修改代码。",
    target: "#feedback",
    action: "记录一条改进反馈",
    availability: "已实现 · 审批保护"
  },
  {
    id: "events",
    title: "星轨日志 · 回看系统发生过什么",
    purpose: "追踪近期研究、任务、Agent 与系统活动，帮助你判断工作是否真的发生。",
    howToUse: "在「星轨事件」按时间查看最近记录。界面会接收结构化事件流，并定期刷新状态；需要核对当前数据时点击顶部刷新。",
    result: "事件显示类别、时间与来源等摘要信息，可与任务、产品时间线互相对照。",
    boundary: "事件摘要不是完整执行日志，也不会展示敏感任务 payload。当前不是无限期审计浏览器。",
    target: "#events",
    action: "查看星轨日志",
    availability: "已实现 · 有界事件流"
  }
];

export function getGuideStep(index) {
  const safe = Number.isFinite(index) ? Math.trunc(index) : 0;
  return USER_GUIDE_STEPS[Math.max(0, Math.min(USER_GUIDE_STEPS.length - 1, safe))];
}

export function getGuideProgress(index) {
  const current = Math.max(0, Math.min(USER_GUIDE_STEPS.length - 1, Number.isFinite(index) ? Math.trunc(index) : 0)) + 1;
  return {
    current,
    total: USER_GUIDE_STEPS.length,
    percent: Math.round((current / USER_GUIDE_STEPS.length) * 100)
  };
}
