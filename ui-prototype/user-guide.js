export const USER_GUIDE_STEPS = [
  {
    id: "overview",
    title: "赛博星环浑天仪 · 一眼看清系统与星象透镜",
    purpose: "这里把本地 API 核心状态、硬件算力护栏、普罗米修斯五阶账本、八卦透镜与二十八星宿集成到统一星盘。",
    howToUse: "点击外环二十八星宿、中环八卦或内环五阶节点，右侧检视舱会立即显示对应系统透镜与真实账本计数；点击顶部可切换全息动态或静态护眼模式。",
    result: "你能 5 秒内掌握核心是否在线、推荐安全并发数、GPU 显存状态（未测标 UNKNOWN），以及当前干支时空文化参考。",
    boundary: "总览数字全部取自真实本机库；八卦、星宿和干支仅作结构隐喻与文化参考，不是事实证据或预测。",
    target: "#overview",
    action: "查看星环总览",
    availability: "已实现 · 真实状态"
  },
  {
    id: "celestial-lab",
    title: "八卦与星宿假设工坊 · 把文化符号转为可证伪提问",
    purpose: "把易经六十四卦（本/之/互/错/综）、四象二十八星宿与奇门/紫微/大六壬框架转化为可检验的研究起点。",
    howToUse: "选择「种子三钱模拟」「上下八卦合卦」或「手动六爻输入」，填写探索目标后点击「运行确定性八卦推演」；如需后续验证，点「存入本地灵感候选」。",
    result: "生成确定性卦象结构与四体系对照提问，并可作为待验证假设写入本地灵感池。",
    boundary: "明确标注 symbolic_hypothesis_only 与 is_evidence=false；绝不自动写回 User Track 记忆，也不会自动批准任务。",
    target: "#celestial-lab",
    action: "前往八卦工坊",
    availability: "已实现 · 假设引擎"
  },
  {
    id: "knowledge-starmap",
    title: "知识星图 · 漫游 Research Wiki 宇宙",
    purpose: "直观浏览本机持久化的 claim / idea / source / experiment / decision / failure 星体及关系网络。",
    howToUse: "在星图画布上拖拽平移、滚轮或右上按钮缩放、按节点类型与关键词筛选；点击任意星体后，可在右侧档案栏点击邻居星体连续跃迁。",
    result: "清楚看到知识连接结构、来源 provenance、证据候选数，以及孤立节点和待人工审查证据的真实治理欠账。",
    boundary: "星图底部诚实展示未审查候选与无证据主张数量；候选证据未经人工审阅前不会晋升为已验证事实。",
    target: "#knowledge-starmap",
    action: "探索知识星图",
    availability: "已实现 · 只读星图"
  },
  {
    id: "agents",
    title: "AI Agent 星系 · 唯一 Commander 与路由沙盒",
    purpose: "展示唯一总调度权威 Muse Commander、8 大专家角色模型、角色安全策略、实连 Agent 与历史编排战绩。",
    howToUse: "点击内环专家行星查看角色信任等级与历史运行统计；在右侧「统帅路由沙盒预演」输入任务描述并点击预演，点亮匹配的专家节点。",
    result: "看清哪些专家只是模板元数据（待接入执行器），哪些真实 Agent 已连接在线。",
    boundary: "严格区分实连 Agent 与模板元数据；路由预演为纯只读仿真，不会自动创建或派发任务。",
    target: "#agents",
    action: "查看 Agent 星系",
    availability: "部分实现 · 模板就绪"
  },
  {
    id: "prometheus-core",
    title: "普罗米修斯核心 · 能力与长期愿景全景矩阵",
    purpose: "把「点火 → 求知 → 融合 → 创造 → 传承」五阶真实账本、12 项演化哲学原则与当前/未来能力盘点汇总于一处。",
    howToUse: "使用矩阵顶部的状态筛选器切换查看「已实现」「部分实现·本机依赖」「仅后端/CLI」与「长期愿景」。",
    result: "清楚区分哪些功能今天即可双击运行，哪些能力受本地依赖约束，哪些属于长期研发愿景。",
    boundary: "绝不把概念愿景包装成已上线能力；跨媒介无限创造与系统级常驻服务仍属长期路线。",
    target: "#prometheus-core",
    action: "查看普罗米修斯核心",
    availability: "已实现 · 全景盘点"
  },
  {
    id: "inspiration",
    title: "灵感工坊与价值创作台 · 评估与入池",
    purpose: "从六维启发式评分筛选高价值选题，并将灵感、问题或故事种子存入本地待研究池。",
    howToUse: "先在「价值创作台」评估灵感，再决定「存入灵感池」或带入视频创作；在「灵感工坊」勾选联网许可后才允许进入公开资料检索。",
    result: "灵感进入历史列表，研究生成的 Markdown 报告会出现在右侧供下载。",
    boundary: "六维评分仅为排序启发，不预测流量或收益；保存灵感不等于自动研究完成。",
    target: "#value-studio",
    action: "前往价值创作台",
    availability: "已实现 · 本地保存"
  },
  {
    id: "video",
    title: "视频创作与成果库 · 生产、预览与人工审片",
    purpose: "支持本机 ComfyUI Wan 2.1 文生视频与 FFmpeg/Remotion 本地素材合成，并提供预览、下载与粤语字幕逐条审校。",
    howToUse: "选择制作方式并填写标题/描述或素材文件夹，确认引擎状态后提交；成片进入成果库后可预览、下载 MP4/ZIP、查看时间线并提交审片意见。",
    result: "生成经过 SHA-256 校验与技术 QA 的成品，审片反馈会回到 Creator Initiative 供后续改进。",
    boundary: "AI 模式依赖本机 ComfyUI/Wan 服务；所有作品初始为 testing，须人工审片确认。",
    target: "#products",
    action: "查看视频与成果库",
    availability: "部分实现 · 本机依赖"
  },
  {
    id: "queue",
    title: "自主运行时 & 任务队列 · 可控守护与监控",
    purpose: "在本地按需启动/暂停/恢复自主监督循环，运行一键短片配方，并只读观察 Commander 队列与 Worker 心跳。",
    howToUse: "在「自主运行时」按需启停循环；在「任务队列」按名称或状态搜索排队、执行中、待审批或失败任务。",
    result: "操作记录写入 control-log.jsonl 可审计，不会在未授权时开机自启或安装系统服务。",
    boundary: "队列面板为只读监控；敏感任务批准、取消与重试保留 CLI 显式审批保护。",
    target: "#daemon-runtime",
    action: "查看运行时与队列",
    availability: "已实现 · 受控运行"
  },
  {
    id: "evolution",
    title: "进化实验室与星轨日志 · 反馈闭环",
    purpose: "录入结构化标签反馈、触发 24 小时能力雷达扫描、检查提案来源与回滚方案，并实时接收星轨事件流。",
    howToUse: "选择类别、标签与严重度提交反馈；点击「扫描改进机会」生成或更新提案并展开详情审查。",
    result: "提案带有验收标准与回滚计划，供人工审查决策。",
    boundary: "扫描与反馈绝不会自动改写代码或自动批准提案；提案者不能批准自己。",
    target: "#feedback",
    action: "查看进化实验室",
    availability: "已实现 · 审批保护"
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
