/**
 * Muse 价值创作台 · 选题价值评分引擎
 *
 * 把一段灵感/选题文本转成结构化的创作建议。这是「排序与启发」工具，
 * 不是流量或收益预测；所有输出都带有置信与性质标记，供人判断。
 *
 * 评分维度与权重来自 docs/MUSE_HIGH_VALUE_VIDEO_FACTORY.md 第 3 节：
 * 需求强度 25% / 证据可核验 20% / 原创差异化 20% /
 * 可视化叙事 15% / 长期复用 10% / 生产成本可控 10%。
 */

export const VALUE_DIMENSIONS = [
  {
    key: 'demand',
    label: '需求强度',
    hint: '受众痛点是否真实、具体',
    weight: 0.25,
    keywords: ['痛点', '问题', '解决', '如何', '怎么', '为什么', '教程', '入门', '避坑', '指南', '测评', '对比', '揭秘', '真相', '误区', '方法', '技巧', '攻略', 'need', 'how to', 'why', 'problem', 'guide', 'tips', 'review', 'vs'],
  },
  {
    key: 'evidence',
    label: '证据可核验',
    hint: '能否用数据与来源支撑',
    weight: 0.2,
    keywords: ['数据', '研究', '报告', '论文', '官方', '实测', '实验', '来源', '依据', '案例', '统计', '验证', '复现', '文献', '数据对比', 'research', 'data', 'study', 'source', 'evidence', 'proof', 'benchmark'],
  },
  {
    key: 'originality',
    label: '原创差异化',
    hint: '角度是否独特、非搬运',
    weight: 0.2,
    keywords: ['独特', '反向', '另类', '不为人知', '冷知识', '颠覆', '新解', '跨界', '融合', '第一视角', '亲身', '独家', '重新', '从未', '被忽略', 'unique', 'reverse', 'hidden', 'cross', 'fusion', 'first', 'original', 'unknown'],
  },
  {
    key: 'visual',
    label: '可视化叙事',
    hint: '是否适合镜头与动画表达',
    weight: 0.15,
    keywords: ['动画', '演示', '过程', '对比', '镜头', '画面', '故事', '场景', '时间线', '拆解', '工作流', '可视化', '还原', '模拟', '3d', 'animation', 'demo', 'scene', 'story', 'timeline', 'visual', 'process', 'workflow'],
  },
  {
    key: 'evergreen',
    label: '长期复用',
    hint: '能否长期搜索与系列化',
    weight: 0.1,
    keywords: ['原理', '基础', '方法论', '合集', '速查', '经典', '长期', '系列', '体系', '本质', '底层', '收藏', 'fundamental', 'principle', 'series', 'cheatsheet', 'classic', 'evergreen'],
  },
  {
    key: 'feasibility',
    label: '成本可控',
    hint: '本机是否可持续生产',
    weight: 0.1,
    keywords: ['短片', '一分钟', '简单', '固定机位', '图文', '口播', '录屏', '模板', '复用', '素材', '竖屏', '短视频', 'quick', 'short', 'simple', 'template', 'reuse', 'screen recording', 'voiceover'],
  },
];

/** 高风险措辞：命中即降低可信度并给出「待核实」标记，不直接否决创意。 */
const RISK_PATTERNS = [
  { code: 'income_guarantee', label: '收益承诺措辞', words: ['稳赚', '保证赚钱', '必赚', '暴富', '躺赚', '月入过万', 'guaranteed', 'rich quick'] },
  { code: 'virality_claim', label: '流量确定性措辞', words: ['必火', '一定爆', '百分百爆款', 'viral guaranteed'] },
  { code: 'rights_risk', label: '版权/来源风险', words: ['搬运', '盗版', '破解版', '无版权', '免费下载资源', 'rip', 'pirated'] },
  { code: 'sensitive_claim', label: '高风险断言', words: ['包治', '根治', '内幕消息', '稳赢'] },
];

const SENSIBLE_MAX = 5;
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

function normalize(text) {
  return String(text ?? '').toLowerCase().replace(/\s+/g, ' ').trim();
}

function countHits(haystack, words) {
  let hits = 0;
  const matched = [];
  for (const word of words) {
    const needle = word.toLowerCase();
    if (!needle) continue;
    if (haystack.includes(needle)) {
      hits += 1;
      if (matched.length < 4) matched.push(word);
    }
  }
  return { hits, matched };
}

/** 结构信号：数字、问句、明确受众、对比结构都让选题更可执行。 */
function structureSignals(raw, normalized) {
  const signals = [];
  if (/\d/.test(raw)) signals.push('包含具体数字');
  if (/[?？]/.test(raw) || /\b(how|why|what)\b/.test(normalized)) signals.push('提出明确问题');
  if (/(你|你们|新手|小白|玩家|用户|家长|上班族|开发者|创作者|观众)/.test(raw)) signals.push('指定目标受众');
  if (/(vs|对比|还是|区别|之争|before|after)/i.test(raw)) signals.push('具备对比结构');
  if (raw.length >= 24) signals.push('描述足够具体');
  return signals;
}

/**
 * 对选题文本评分。
 * @param {string} text 灵感/选题描述
 * @returns {object} 结构化评分；不访问网络、无副作用
 */
export function scoreIdea(text) {
  const raw = String(text ?? '').trim();
  const normalized = normalize(raw);
  const structure = structureSignals(raw, normalized);
  const scores = VALUE_DIMENSIONS.map((dimension) => {
    const { hits, matched } = countHits(normalized, dimension.keywords);
    let value = 1.5 + Math.min(hits, 4) * 0.875; // 1.5 中性基线 + 关键词命中
    if (structure.includes('包含具体数字') && (dimension.key === 'evidence' || dimension.key === 'demand')) {
      value += 0.5;
    }
    if (dimension.key === 'visual' && structure.includes('具备对比结构')) value += 0.5;
    if (dimension.key === 'demand' && structure.includes('提出明确问题')) value += 0.5;
    const score = clamp(Math.round(value * 2) / 2, 0, SENSIBLE_MAX);
    return {
      key: dimension.key,
      label: dimension.label,
      hint: dimension.hint,
      weight: dimension.weight,
      score,
      matched: matched.slice(0, 4),
    };
  });

  const weighted = scores.reduce((sum, item) => sum + item.score * item.weight, 0); // 0-5
  const riskFlags = RISK_PATTERNS.filter((pattern) =>
    pattern.words.some((word) => normalized.includes(word.toLowerCase()))
  ).map(({ code, label }) => ({ code, label }));
  const riskPenalty = riskFlags.reduce((sum, flag) => sum + (flag.code === 'rights_risk' ? 12 : 7), 0);
  const total = clamp(Math.round((weighted / SENSIBLE_MAX) * 100 - riskPenalty), 0, 100);

  const tier = total >= 82 ? 'S' : total >= 68 ? 'A' : total >= 50 ? 'B' : 'C';
  const byKey = Object.fromEntries(scores.map((item) => [item.key, item.score]));

  const suggestedPath = byKey.visual >= 3.5 && byKey.feasibility >= 3
    ? 'AI 镜头生成 + Remotion 动效包装'
    : byKey.evidence >= 3.5
      ? '研究型竖屏短片（口播 + 数据动画）'
      : '本地素材剪辑 + 自动字幕与封面';

  const potentialTags = [];
  if (byKey.demand >= 3.5) potentialTags.push('明确受众');
  if (byKey.evidence >= 3.5) potentialTags.push('信任型内容');
  if (byKey.originality >= 3.5) potentialTags.push('差异化角度');
  if (byKey.evergreen >= 3.5) potentialTags.push('长尾复用');
  if (byKey.visual >= 3.5) potentialTags.push('强可视化');
  if (!potentialTags.length) potentialTags.push('待细化选题');

  const suggestedPlatforms = [];
  if (byKey.visual >= 3 || byKey.demand >= 3) suggestedPlatforms.push('抖音 / 视频号');
  if (byKey.evidence >= 3 || byKey.evergreen >= 3) suggestedPlatforms.push('哔哩哔哩');
  if (byKey.originality >= 3 || byKey.visual >= 3) suggestedPlatforms.push('YouTube Shorts');
  if (!suggestedPlatforms.length) suggestedPlatforms.push('先做本地样片再选平台');

  return {
    schemaVersion: 1,
    nature: 'heuristic_estimate',
    label: '启发式辅助评分 · 不构成流量或收益预测',
    scores,
    structure,
    riskFlags,
    total,
    tier,
    suggestedPath,
    potentialTags,
    suggestedPlatforms,
    monetizationNote: '变现方向仅为选题假设；需真实平台授权与官方数据回流后才能计入表现或收入。',
    wordCount: raw.length,
    empty: raw.length === 0,
  };
}

/** 把评分结果序列化为可存入灵感工坊的简短说明，保留来源与性质标记。 */
export function toInspirationBrief(result, originalText) {
  const dimensionLine = result.scores
    .map((item) => `${item.label} ${item.score}/5`)
    .join(' · ');
  const riskLine = result.riskFlags.length
    ? ` · 待核实：${result.riskFlags.map((flag) => flag.label).join('、')}`
    : '';
  return [
    String(originalText ?? '').trim(),
    '',
    `[Muse 价值创作台 · 启发式评估 ${result.tier} / ${result.total}]`,
    dimensionLine,
    `建议路径：${result.suggestedPath}${riskLine}`,
    `性质标记：${result.label}`,
    result.monetizationNote,
  ].join('\n');
}
