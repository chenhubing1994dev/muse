// B391 P2 · 视图 1：Muse 普罗米修斯计划品牌门户（/portal）。
// 所有数据来自本机接口；服务端文本一律 textContent 渲染。
import { call, el, toast } from "./omega-common.js";
import { buildBagua, mountStarfield, ring } from "./cosmos-fx.js";
import { METAPHYSICS_LABEL, ROADMAP, healthChecks, pollDelay } from "./muse-os-logic.js";

const LOOP_ICONS = { inspiration: "💡", research: "📖", knowledge: "🌳", muse: "∞", creation: "🎬", verification: "🛡", evolution: "🚀" };

const state = { summary: null, readiness: null, health: null, agents: null, reviewer: null, proposals: null };

async function soft(path) {
  try { return await call(path); } catch (error) { return { __error: error.message }; }
}

function renderLoop() {
  const track = document.getElementById("loop-track");
  const loop = state.summary && Array.isArray(state.summary.workflow_loop) ? state.summary.workflow_loop : [];
  track.replaceChildren();
  if (!loop.length) {
    track.append(el("div", "无法读取 Commander 汇总：" + (state.summary && state.summary.__error ? state.summary.__error : "未连接"), "empty"));
    return;
  }
  for (const node of loop) {
    const button = el("button", "", "loop-node" + (node.id === "muse" ? " core" : ""));
    button.type = "button";
    button.setAttribute("aria-pressed", "false");
    button.append(el("span", LOOP_ICONS[node.id] || "✦", "orb"), el("span", node.label), el("em", node.value === null || node.value === undefined ? "—" : String(node.value)));
    button.addEventListener("click", () => showLoopDetail(node, button));
    track.append(button);
  }
  document.getElementById("loop-updated").textContent = "已读取 " + new Date().toLocaleTimeString("zh-CN");
}

function showLoopDetail(node, button) {
  for (const other of document.querySelectorAll(".loop-node")) other.setAttribute("aria-pressed", "false");
  button.setAttribute("aria-pressed", "true");
  const box = document.getElementById("loop-detail");
  box.classList.remove("hidden");
  const s = state.summary || {};
  const facts = {
    inspiration: ["Research Wiki 灵感节点", (s.knowledge && s.knowledge.nodes_by_type && s.knowledge.nodes_by_type.idea) || 0],
    research: ["已入库来源节点", (s.knowledge && s.knowledge.nodes_by_type && s.knowledge.nodes_by_type.source) || 0],
    knowledge: ["知识节点 / 关系边", (s.knowledge && s.knowledge.nodes !== undefined) ? s.knowledge.nodes + " / " + s.knowledge.edges : "未初始化"],
    muse: ["Commander 运行中 / 排队", s.queue ? s.queue.running + " / " + s.queue.queued : "未知"],
    creation: ["Product Vault 视频成片", s.products ? s.products.videos : 0],
    verification: ["待人工审批的 Ticket", s.queue ? s.queue.waiting_approval : "未知"],
    evolution: ["Agent Track 工作经验", s.memory && s.memory.agent !== undefined ? s.memory.agent : "未初始化"],
  }[node.id] || ["—", "—"];
  box.replaceChildren(
    el("h3", node.label),
    el("p", facts[0] + "：" + facts[1]),
    (() => { const a = el("a", "进入 Super Agent OS 对应工作台 »", "btn qi"); a.href = "./os.html#" + node.os_view; return a; })(),
  );
}

function capabilityCards() {
  const s = state.summary || {};
  const agents = state.agents && Array.isArray(state.agents.agents) ? state.agents.agents : [];
  const enabled = agents.filter((agent) => agent.enabled !== false).length;
  const r = state.readiness || {};
  const pending = Array.isArray(state.proposals && state.proposals.proposals)
    ? state.proposals.proposals.filter((p) => p.status === "ready_for_review").length : null;
  return [
    { glyph: "🜂", title: "AI 多智能体协同", sub: "Commander 统帅全局",
      items: ["唯一 Commander 调度与审批", "并发上限 32，受真实资源约束", "无在线 Agent 时如实显示阻塞"],
      status: agents.length ? enabled + " / " + agents.length + " 个已登记 Agent" : "注册表中暂无 Agent", tone: agents.length ? "ok" : "warn", view: "agents" },
    { glyph: "🌳", title: "研究知识库", sub: "Research Wiki",
      items: ["idea / claim / source 等 6 类节点", "证据独立性审查", "tested_by 关联作品回链"],
      status: s.knowledge && s.knowledge.status === "available" ? s.knowledge.nodes + " 节点 · " + s.knowledge.edges + " 关系" : "尚未初始化",
      tone: s.knowledge && s.knowledge.status === "available" ? "ok" : "warn", view: "knowledge" },
    { glyph: "☯", title: "玄学灵感引擎", sub: "东方智慧 · 西方科技",
      items: ["易经 · 紫微 · 奇门 · 六壬", "象征观察 → 可检验假设", "强制标注「" + METAPHYSICS_LABEL + "」"],
      status: METAPHYSICS_LABEL, tone: "meta", view: "toolbox" },
    { glyph: "🎬", title: "视频生产工厂", sub: "从想法到成片",
      items: ["本地动效：TTS + ASR + Remotion + FFmpeg", "AI 镜头：审批 + 准入 + GPU 租约", "ffprobe 质检 + SHA-256 + ZIP"],
      status: r.chosen_path === "ai_shot_generation" ? "AI 镜头路径就绪" : r.chosen_path === "local_motion" ? "本地动效路径就绪" : r.__error ? "读取失败" : "缺少能力：" + missingText(r),
      tone: r.chosen_path && r.chosen_path !== "blocked" ? "ok" : "bad", view: "toolbox" },
    { glyph: "🧬", title: "自我进化系统", sub: "签名审查门控",
      items: ["失败复盘 → 改进提案", "独立 Reviewer 审查", "用户授权后沉淀经验"],
      status: pending === null ? "提案库未配置" : pending + " 条提案待审查", tone: pending ? "warn" : "ok", view: "tasks" },
  ];
}

function missingText(readiness) {
  const local = readiness.paths && readiness.paths.local_motion;
  return local && Array.isArray(local.missing) && local.missing.length ? local.missing.join("、") : "未知";
}

function renderCaps() {
  const host = document.getElementById("capabilities");
  host.replaceChildren();
  for (const card of capabilityCards()) {
    const node = el("article", "", "glass cap");
    const list = el("ul");
    for (const item of card.items) list.append(el("li", item));
    const badge = el("span", card.status, "badge " + (card.tone === "meta" ? "label-meta" : "tone-" + card.tone));
    const link = el("a", "进入 »", "btn small qi");
    link.href = "./os.html#" + card.view;
    const foot = el("div", "", "foot");
    foot.append(badge, link);
    const title = el("h3", card.title);
    title.append(el("small", card.sub));
    node.append(el("div", card.glyph, "glyph"), title, list, foot);
    host.append(node);
  }
}

let renderedVideoKey = null;
function renderResult() {
  const box = document.getElementById("result-body");
  const latest = state.summary && state.summary.products ? state.summary.products.latest_video : null;
  // B391-P2-25: polling must not recreate the <video> (that stopped playback every 30 s).
  const key = latest ? [latest.video_path, latest.video_mp4_sha256, JSON.stringify(latest.quality_gate || {})].join("|") : "none";
  if (key === renderedVideoKey && box.childElementCount) return;
  renderedVideoKey = key;
  box.replaceChildren();
  if (!latest) {
    box.append(el("div", "Product Vault 中还没有真实成片。这里不会放占位视频。", "empty"));
    const go = el("a", "立即生成首部真实作品 »", "btn primary");
    go.href = "./os.html#toolbox";
    box.append(go);
    return;
  }
  const video = document.createElement("video");
  video.controls = true; video.preload = "metadata";
  video.src = latest.video_path;
  const gate = latest.quality_gate || {};
  const qa = gate.status === "passed" ? ["技术 QA 通过", "ok"] : gate.status === "failed" ? ["技术 QA 未通过", "bad"] : ["QA 状态未知", "warn"];
  const row = el("div", "", "row");
  row.append(el("span", qa[0], "badge tone-" + qa[1]),
    el("span", gate.ready_to_publish ? "可发布" : "仍需人工内容/版权审片", "badge tone-" + (gate.ready_to_publish ? "ok" : "warn")));
  box.append(el("h3", latest.name || "Muse 作品"), video, row,
    el("p", "MP4 SHA-256：" + (latest.video_mp4_sha256 || "（清单未提供）"), "mono"));
  if (latest.download_path) {
    const zip = el("a", "下载工程 ZIP", "btn small");
    zip.href = latest.download_path;
    box.append(zip);
  }
}

function renderHealth() {
  const result = healthChecks({
    health: state.health, summary: state.summary, readiness: state.readiness,
    signedReviewer: !!(state.reviewer && state.reviewer.key && state.reviewer.key.initialized && state.reviewer.key.trust_store_ok),
  });
  ring(document.getElementById("health-ring"), result.percent, "实时健康度");
  const list = document.getElementById("health-list");
  list.replaceChildren();
  for (const check of result.checks) {
    const li = el("li");
    li.append(el("span", check.label), el("span", check.ok ? "已就绪" : "未就绪", "badge tone-" + (check.ok ? "ok" : "warn")));
    list.append(li);
  }
  document.getElementById("health-note").textContent = `实时判定 ${result.passed}/${result.total} 项就绪；未就绪项会在 Super Agent OS 中给出阻塞原因。`;
}

function renderRoad() {
  const road = document.getElementById("road");
  road.replaceChildren();
  for (const stop of ROADMAP) {
    const node = el("div", "", "stop");
    node.append(el("i"), el("b", stop.label), el("small", stop.detail));
    road.append(node);
  }
}

async function load() {
  const [summary, readiness, health, agents, reviewer, proposals] = await Promise.all([
    soft("/control/summary"), soft("/control/creation/readiness"), soft("/health"), soft("/agents"),
    soft("/reviewer-key/status"), soft("/improvements"),
  ]);
  Object.assign(state, { summary, readiness, health, agents, reviewer, proposals });
  if (summary.__error) toast("无法连接 Muse 本地接口：" + summary.__error, "bad");
  renderLoop(); renderCaps(); renderResult(); renderHealth();
}

mountStarfield(document.getElementById("starfield"));
buildBagua(document.getElementById("bagua"), { coreText: "∞", subText: "自主进化 · 永无止境" });
renderRoad();
let pollHandle = 0;
function schedule() {
  window.clearTimeout(pollHandle);
  pollHandle = window.setTimeout(async () => { await load(); schedule(); }, pollDelay(30000, document.hidden));
}
load(); schedule();
document.addEventListener("visibilitychange", () => { if (!document.hidden) load(); schedule(); });
window.addEventListener("pagehide", () => window.clearTimeout(pollHandle));
