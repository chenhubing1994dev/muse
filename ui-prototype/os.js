// B391 P2 · 视图 2：Muse Super Agent OS 超级智能体调度中心（/os）。
// 8 个模块全部接真实接口；所有服务端文本 textContent 渲染；所有写操作需要人工确认。
import { call, el, toast } from "./omega-common.js";
import { mountStarfield, ring, svg } from "./cosmos-fx.js";
import {
  AGENT_ROLES, EDGE_TYPE_LABELS, METAPHYSICS_LABEL, NODE_TYPE_COLORS, NODE_TYPE_LABELS, OS_VIEWS,
  agentPresence, evidenceChainView, layoutGraph, lineDiff, phraseProgress, pollDelay, requestId, resourceBars,
  rosterFromRegistry, successRate, ticketStatus,
} from "./muse-os-logic.js";

const $ = (id) => document.getElementById(id);
const state = {
  view: "overview", track: "user", graph: null, graphLayout: [], memories: [], readiness: null, readinessAt: 0,
  lastTasks: null, lastApprovals: "", lastOverview: "", lastProducts: "", drafts: new Map(), claims: null, claimsAt: 0,
  timers: new Set(), pollHandle: 0,
};
const READINESS_TTL_MS = 60000;

// ───────── confirmation gate (B391-P2-23) ─────────
// Shows the exact phrase the backend checks, offers “一键填入” (fills the box only — the human still clicks
// 确认提交), validates spelling live, keeps errors (with HTTP code) inside the dialog and blocks double submits.
const gate = { open: false, resolve: null, action: null, lastFocus: null };
function openGate({ title, detail, phrase, withReason = false, action }) {
  if (gate.open) closeGate(null);
  gate.open = true; gate.action = action; gate.lastFocus = document.activeElement;
  $("gate-title").textContent = title;
  $("gate-detail").textContent = detail || "";
  $("gate-phrase").textContent = phrase;
  $("gate").dataset.phrase = phrase;
  const input = $("gate-input"); input.value = ""; input.className = "";
  $("gate-check").textContent = "请输入上方短语，或点击“一键填入”。"; $("gate-check").className = "gate-check";
  $("gate-reason-wrap").classList.toggle("hidden", !withReason); $("gate-reason").value = "";
  $("gate-error").classList.add("hidden"); $("gate-error").textContent = "";
  $("gate-submit").disabled = true; $("gate-submit").removeAttribute("aria-busy");
  $("gate").classList.remove("hidden");
  window.setTimeout(() => input.focus(), 0);
  return new Promise((resolve) => { gate.resolve = resolve; });
}
function closeGate(result) {
  if (!gate.open) return;
  gate.open = false;
  $("gate").classList.add("hidden");
  const resolve = gate.resolve; gate.resolve = null; gate.action = null;
  if (gate.lastFocus && typeof gate.lastFocus.focus === "function") gate.lastFocus.focus();
  if (resolve) resolve(result);
}
function gateCheck() {
  const phrase = $("gate").dataset.phrase || "";
  const input = $("gate-input");
  const progress = phraseProgress(input.value, phrase);
  input.className = progress.state === "match" ? "match" : progress.state === "mismatch" ? "mismatch" : "";
  const label = { empty: "请输入上方短语，或点击“一键填入”。", partial: "已输入 " + progress.prefix + " / " + phrase.length + " 个字符…",
    match: "✓ 短语正确，可以提交。", mismatch: "✗ 第 " + (progress.prefix + 1) + " 个字符起与短语不一致（区分大小写）。" }[progress.state];
  $("gate-check").textContent = label; $("gate-check").className = "gate-check " + progress.state;
  $("gate-submit").disabled = !progress.ok || $("gate-submit").getAttribute("aria-busy") === "true";
}
async function gateSubmit(event) {
  event.preventDefault();
  const submit = $("gate-submit");
  const phrase = $("gate").dataset.phrase || "";
  if (!phraseProgress($("gate-input").value, phrase).ok || submit.getAttribute("aria-busy") === "true") return;
  submit.disabled = true; submit.setAttribute("aria-busy", "true"); $("gate-cancel").disabled = true;
  $("gate-error").classList.add("hidden");
  try {
    const result = await gate.action({ confirmation: $("gate-input").value, reason: $("gate-reason").value.trim() });
    closeGate(result === undefined ? true : result);
  } catch (error) {
    $("gate-error").textContent = "操作失败：" + error.message; $("gate-error").classList.remove("hidden");
  } finally {
    submit.removeAttribute("aria-busy"); $("gate-cancel").disabled = false; if (gate.open) gateCheck();
  }
}
function bindGate() {
  $("gate-input").addEventListener("input", gateCheck);
  $("gate-fill").addEventListener("click", () => { $("gate-input").value = $("gate").dataset.phrase || ""; gateCheck(); $("gate-submit").focus(); });
  $("gate-cancel").addEventListener("click", () => closeGate(null));
  $("gate-form").addEventListener("submit", gateSubmit);
  $("gate").addEventListener("click", (event) => { if (event.target === $("gate")) closeGate(null); });
}
function debounce(fn, ms) {
  let handle = 0;
  return (...args) => { window.clearTimeout(handle); handle = window.setTimeout(() => fn(...args), ms); };
}
function userIsEditing(scope) {
  const active = document.activeElement;
  return Boolean(gate.open || (active && scope && scope.contains(active) && ["INPUT", "TEXTAREA", "SELECT"].includes(active.tagName)));
}

async function soft(path, options) {
  try { return await call(path, options); } catch (error) { return { __error: error.message }; }
}
function badge(text, tone) { return el("span", text, "badge tone-" + tone); }
function button(text, cls, onClick) {
  const node = el("button", text, "btn small " + (cls || ""));
  node.type = "button";
  node.addEventListener("click", async () => {
    if (node.disabled) return;
    node.disabled = true;
    try { await onClick(); } catch (error) { toast(error.message, "bad"); } finally { node.disabled = false; }
  });
  return node;
}
function kv(target, pairs) {
  target.replaceChildren();
  for (const [key, value] of pairs) target.append(el("dt", key), el("dd", value === null || value === undefined || value === "" ? "—" : String(value)));
}
function rows(target, items, columns, empty) {
  target.replaceChildren();
  if (!items || !items.length) { const td = el("td", empty || "暂无数据", "muted"); const tr = el("tr"); tr.append(td); target.append(tr); return; }
  const head = el("tr");
  for (const [, label] of columns) head.append(el("th", label));
  const thead = el("thead"); thead.append(head);
  const tbody = el("tbody");
  for (const item of items) {
    const tr = el("tr");
    for (const [key] of columns) {
      const td = el("td");
      const value = typeof key === "function" ? key(item) : item[key];
      if (value instanceof Node) td.append(value);
      else td.textContent = value === null || value === undefined ? "—" : (typeof value === "object" ? JSON.stringify(value) : String(value));
      tr.append(td);
    }
    tbody.append(tr);
  }
  target.append(thead, tbody);
}
function short(id) { return typeof id === "string" ? id.slice(0, 8) : "—"; }
function when(value) { const t = Date.parse(value); return Number.isFinite(t) ? new Date(t).toLocaleString("zh-CN", { hour12: false }) : "—"; }

// ───────── navigation ─────────
function buildNav() {
  const nav = $("os-nav");
  const spirit = nav.querySelector(".spirit");
  for (const view of OS_VIEWS) {
    const b = el("button");
    b.type = "button"; b.dataset.view = view.id;
    b.append(el("span", view.icon, "ic"), el("span", view.label));
    b.addEventListener("click", () => go(view.id));
    nav.insertBefore(b, spirit);
  }
  for (const b of $("dock").querySelectorAll("button[data-go]")) {
    b.addEventListener("click", () => { go(b.dataset.go); if (b.dataset.focus) window.setTimeout(() => $(b.dataset.focus)?.scrollIntoView({ behavior: "smooth" }), 50); });
  }
  window.addEventListener("hashchange", () => go(location.hash.slice(1), false));
}
function go(view, push = true) {
  if (!OS_VIEWS.some((item) => item.id === view)) view = "overview";
  state.view = view;
  for (const section of document.querySelectorAll(".view")) section.classList.toggle("active", section.id === "view-" + view);
  for (const b of $("os-nav").querySelectorAll("button[data-view]")) {
    if (b.dataset.view === view) b.setAttribute("aria-current", "page"); else b.removeAttribute("aria-current");
  }
  if (push && location.hash !== "#" + view) history.replaceState(null, "", "#" + view);
  const loaders = { overview: loadOverview, tasks: loadTasks, agents: loadAgents, memory: loadMemory, knowledge: loadKnowledge,
    toolbox: loadToolbox, workflow: loadWorkflow, settings: loadSettings };
  loaders[view]?.();
}

// ───────── window / clock / side ─────────
function windowControls() {
  try {
    const current = window.__TAURI__?.window?.getCurrentWindow?.();
    if (!current) return;
    $("winctl").classList.remove("hidden");
    $("win-min").addEventListener("click", () => current.minimize());
    $("win-max").addEventListener("click", () => current.toggleMaximize());
    $("win-close").addEventListener("click", () => current.close());
  } catch { /* not running inside the Tauri shell */ }
}
function tick() {
  const now = new Date();
  $("clock-time").textContent = now.toLocaleTimeString("zh-CN", { hour12: false });
  $("clock-date").textContent = now.toLocaleDateString("zh-CN", { year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" });
  const hour = now.getHours();
  $("clock-moon").textContent = hour >= 6 && hour < 18 ? "☀" : "☾";
}
async function loadSide() {
  const [health, summary, telemetry, agents, events] = await Promise.all([
    soft("/health"), soft("/control/summary"), soft("/control/telemetry"), soft("/agents"), soft("/events?limit=20"),
  ]);
  $("conn").textContent = health.__error ? "未连接：" + health.__error : health.status === "ok" ? "本机引擎在线" : "降级：" + health.status;
  $("conn").className = "badge tone-" + (health.__error ? "bad" : health.status === "ok" ? "ok" : "warn");
  const list = Array.isArray(agents.agents) ? agents.agents : [];
  const online = list.filter((agent) => ["online", "busy"].includes(agentPresence(agent).state)).length;
  const counts = summary.queue || {};
  const rate = successRate(counts);
  kv($("side-status"), [
    ["引擎状态", health.__error ? "离线" : health.status === "ok" ? "运行中" : health.status],
    ["在线 Agent", list.length ? online + " / " + list.length : "注册表为空"],
    ["任务队列", counts.queued !== undefined ? (counts.queued + counts.running) + "（待审 " + counts.waiting_approval + "）" : "—"],
    ["成功率", rate === null ? "尚无已结束任务" : rate + "%"],
  ]);
  const res = $("side-res"); res.replaceChildren();
  for (const bar of resourceBars(telemetry.__error ? null : telemetry)) {
    const row = el("div", "", "res-row");
    const track = el("div", "", "bar" + (bar.id === "storage" ? " rose" : bar.id === "memory" ? "" : " gold"));
    const fill = el("i"); fill.style.width = (bar.value === null ? 0 : bar.value) + "%"; track.append(fill);
    row.append(el("span", bar.label), track, el("small", (bar.value === null ? "" : bar.value + "% · ") + bar.text));
    res.append(row);
  }
  const feed = $("side-feed"); feed.replaceChildren();
  const items = Array.isArray(events.events) ? events.events : [];
  if (!items.length) feed.append(el("li", events.__error ? "动态读取失败" : "暂无动态", "muted"));
  for (const item of items.slice(0, 12)) feed.append(el("li", `${when(item.created_at)} · ${item.event || item.event_type || ""} ${item.summary || ""}`));
}

// ───────── 总览 ─────────
async function loadOverview(background = false) {
  const [agents, summary, tasks] = await Promise.all([soft("/agents"), soft("/control/summary"), soft("/control/tasks?limit=40")]);
  const fingerprint = JSON.stringify([agents, summary, tasks]);
  if (background && fingerprint === state.lastOverview) return;  // no flicker when nothing changed
  state.lastOverview = fingerprint;
  const roster = rosterFromRegistry(Array.isArray(agents.agents) ? agents.agents : []);
  const orbit = $("orbit"); orbit.replaceChildren();
  const stage = $("stage");
  for (const chip of stage.querySelectorAll(".agent-chip")) chip.remove();
  const positions = { commander: [50, 12], researcher: [28, 28], architect: [72, 28], builder: [18, 66], memory: [82, 66], tester: [38, 86], reviewer: [62, 86] };
  orbit.append(svg("ellipse", { cx: 500, cy: 225, rx: 380, ry: 180, fill: "none", stroke: "rgba(245,197,99,.35)", "stroke-dasharray": "4 8" }));
  orbit.append(svg("ellipse", { cx: 500, cy: 225, rx: 260, ry: 120, fill: "none", stroke: "rgba(34,211,238,.25)" }));
  const avatars = { commander: "🧙", researcher: "📜", architect: "📐", builder: "🤖", memory: "🦊", tester: "🧪", reviewer: "🔍" };
  for (const role of roster) {
    const [x, y] = positions[role.id];
    const presence = role.members.length ? agentPresence(role.members[0]) : { state: "missing", label: "未登记" };
    orbit.append(svg("line", { x1: 500, y1: 215, x2: x * 10, y2: y * 4.7, stroke: presence.state === "online" || presence.state === "busy" ? "rgba(34,211,238,.6)" : "rgba(156,147,191,.25)", "stroke-width": 1.2 }));
    const chip = el("button", "", "agent-chip");
    chip.type = "button";
    chip.style.left = x + "%"; chip.style.top = y + "%";
    const tone = { online: "ok", busy: "live", stale: "warn", never_seen: "warn", disabled: "muted", missing: "bad" }[presence.state] || "muted";
    chip.append(el("span", avatars[role.id], "avatar"), el("b", role.name), el("span", "· " + role.cn + " ·"), badge(presence.label, tone));
    chip.title = role.duty + (role.members.length ? "" : "；注册表中没有该角色的 Agent，派发会被阻塞");
    chip.addEventListener("click", () => go("agents"));
    stage.append(chip);
  }
  const counts = summary.queue || {};
  const rate = successRate(counts);
  ring($("ov-ring"), rate === null ? null : Math.round(rate), "成功率");
  kv($("ov-counts"), [["进行中", counts.running], ["排队中", counts.queued], ["待审批", counts.waiting_approval], ["已完成", counts.succeeded], ["失败", counts.failed]]);
  const running = (Array.isArray(tasks.tasks) ? tasks.tasks : []).filter((t) => ["running", "queued", "waiting_approval"].includes(t.status)).slice(0, 5);
  const box = $("ov-running"); box.replaceChildren();
  if (!running.length) box.append(el("div", tasks.__error ? "读取失败：" + tasks.__error : "当前没有执行中或排队的任务。", "empty"));
  for (const task of running) {
    const status = ticketStatus(task.status);
    const row = el("div", "", "row spread");
    row.append(el("span", task.title), badge(status.label, status.tone));
    box.append(row, el("small", "Ticket " + short(task.id) + " · 更新于 " + when(task.updated_at), "muted"));
  }
  const fresh = state.readiness && !state.readiness.__error && Date.now() - state.readinessAt < READINESS_TTL_MS;
  const readiness = fresh ? state.readiness : await soft("/control/creation/readiness");
  if (!fresh) { state.readiness = readiness; state.readinessAt = Date.now(); }
  const caps = $("ov-caps"); caps.replaceChildren();
  const online = roster.filter((r) => r.members.length).length;
  for (const [label, ok, note] of [
    ["多 Agent 协作", online > 1, online + "/7 个角色已登记"],
    ["全网知识融合（Research Wiki）", summary.knowledge && summary.knowledge.status === "available", summary.knowledge && summary.knowledge.nodes !== undefined ? summary.knowledge.nodes + " 节点" : "未初始化"],
    ["自我进化学习（Agent Track）", summary.memory && summary.memory.agent > 0, summary.memory && summary.memory.agent !== undefined ? summary.memory.agent + " 条经验" : "未初始化"],
    ["本地动效成片", readiness.paths && readiness.paths.local_motion && readiness.paths.local_motion.ready, readiness.chosen_path || "未知"],
  ]) {
    const li = el("li"); li.append(el("span", label), badge(ok ? "可用" : "未就绪", ok ? "ok" : "warn")); li.title = note; caps.append(li);
  }
}

// ───────── 任务中心 ─────────
async function loadTasks(background = false) {
  const status = $("task-filter").value;
  const data = await soft("/control/tasks?limit=100" + (status ? "&status=" + status : ""));
  if (data.__error) { if (!background) toast("任务列表读取失败：" + data.__error, "bad"); return; }
  kv($("task-counts"), Object.entries(data.counts || {}).map(([key, value]) => [ticketStatus(key).label, value]));
  const key = status + "|" + JSON.stringify(data.tasks || []);
  if (background && key === state.lastTasks) { await loadApprovals(true); return; }
  state.lastTasks = key;
  rows($("task-table"), data.tasks, [
    [(t) => { const s = ticketStatus(t.status); return badge(s.label, s.tone); }, "状态"],
    ["title", "标题"], ["kind", "类型"], [(t) => when(t.updated_at), "更新"],
    [(t) => {
      const cell = el("div", "", "row");
      cell.append(button("详情", "", () => showTask(t.id)));
      if (["queued", "waiting_approval"].includes(t.status) && !t.dedicated_gate) cell.append(button("取消", "danger", () => cancelTask(t)));
      if (["failed", "cancelled"].includes(t.status) && !t.dedicated_gate) cell.append(button("重试", "qi", () => retryTask(t)));
      return cell;
    }, "操作"],
  ], "当前筛选下没有 Ticket");
  await loadApprovals(background);
}
async function showTask(id) {
  const data = await call("/control/tasks/" + id);
  const box = $("task-detail"); box.classList.remove("hidden"); box.replaceChildren();
  const task = data.task;
  const close = button("关闭（Esc）", "", () => box.classList.add("hidden"));
  box.append(close, el("h3", task.title), el("p", "Ticket " + task.id + " · " + ticketStatus(task.status).label + (task.approved_by ? " · 批准者 " + task.approved_by : ""), "mono"));
  const workflow = task.workflow;
  if (workflow && Array.isArray(workflow.waves) && workflow.waves.length) {
    const dag = el("div", "", "dag");
    workflow.waves.forEach((wave, index) => {
      const w = el("div", "", "wave"); w.append(el("b", "波次 " + (index + 1)));
      for (const step of (Array.isArray(wave) ? wave : wave.steps || [])) w.append(el("div", typeof step === "string" ? step : (step.step_id || JSON.stringify(step))));
      dag.append(w);
    });
    box.append(el("h3", "DAG 波次"), dag);
  } else {
    box.append(el("p", "该 Ticket 尚未绑定 Commander DAG（审批后由 Commander 规划并写入 workflow.bound）。", "muted"));
  }
  const list = el("ul", "", "feed");
  for (const event of task.events || []) list.append(el("li", `${when(event.created_at)} · ${event.event_type} · ${event.actor || ""} ${event.payload && event.payload.decision ? "· " + event.payload.decision : ""}`));
  box.append(el("h3", "事件时间线"), list);
}
async function cancelTask(task) {
  const done = await openGate({ title: "取消 Ticket", detail: `取消「${task.title}」。已在执行的步骤不会被强制中断。`, phrase: "CANCEL-TASK", withReason: true,
    action: ({ confirmation, reason }) => call(`/control/tasks/${task.id}/cancel`, { body: { confirmation, reason: reason || "cancelled from Muse OS" } }) });
  if (done) { toast("已取消。", "ok"); loadTasks(); }
}
async function retryTask(task) {
  const rid = requestId();  // one request_id per dialog: double clicks and retries stay idempotent
  const done = await openGate({ title: "重试 Ticket", detail: `以新的待审批 Ticket 重新提交「${task.title}」。仍需再次人工批准。`, phrase: "RETRY-TASK",
    action: ({ confirmation }) => call(`/control/tasks/${task.id}/retry`, { body: { confirmation, request_id: rid } }) });
  if (done) { toast("已创建新的待审批 Ticket。", "ok"); loadTasks(); }
}
async function submitTask() {
  const title = $("nt-title").value.trim(); const goal = $("nt-goal").value.trim();
  if (!title || !goal) throw new Error("请填写标题和目标描述");
  const rid = requestId();
  const result = await openGate({ title: "提交给 Commander", detail: "提交后进入待审批，不会直接执行。", phrase: "SUBMIT-TO-COMMANDER",
    action: ({ confirmation }) => call("/control/tasks", { body: { request_id: rid, title, goal, request_kind: $("nt-kind").value, confirmation } }) });
  if (!result) return;
  toast("已提交，等待人工审批：" + short(result.task.id), "ok");
  $("nt-title").value = ""; $("nt-goal").value = "";
  loadTasks();
}
async function loadApprovals(background = false) {
  const [data, video] = await Promise.all([soft("/control/approvals"), soft("/video-approval-requests?limit=30")]);
  const fingerprint = JSON.stringify([data, video]);
  if (background && fingerprint === state.lastApprovals) return;
  state.lastApprovals = fingerprint;
  const desk = $("approval-desk"); desk.replaceChildren();
  const section = (title) => { const h = el("h3", title); desk.append(h); return h; };
  section("Commander Ticket");
  const tickets = Array.isArray(data.commander_tickets) ? data.commander_tickets : [];
  if (!tickets.length) desk.append(el("div", data.__error ? "读取失败：" + data.__error : "没有待审批的 Commander Ticket。", "empty"));
  for (const t of tickets) {
    const row = el("div", "", "row spread glass pad");
    const info = el("div"); info.append(el("b", t.title), el("div", `Ticket ${short(t.id)} · ${t.kind} · 通道：${t.approval_channel}`, "muted"));
    const actions = el("div", "", "row");
    if (t.can_decide_here) {
      actions.append(button("批准", "qi", async () => {
        const done = await openGate({ title: "批准 Ticket", detail: `批准「${t.title}」。批准只会让 Ticket 进入排队，仍由 Commander 规划与派发。`, phrase: "APPROVE-TICKET",
          action: ({ confirmation }) => call(`/control/approvals/${t.id}/decide`, { body: { decision: "approve", confirmation } }) });
        if (done) { toast("已批准并写入 human.decision。", "ok"); loadTasks(); }
      }), button("驳回", "danger", async () => {
        const done = await openGate({ title: "驳回 Ticket", detail: `驳回「${t.title}」。Ticket 会被取消并记录原因。`, phrase: "REJECT-TICKET", withReason: true,
          action: ({ confirmation, reason }) => call(`/control/approvals/${t.id}/decide`, { body: { decision: "reject", confirmation, reason } }) });
        if (done) { toast("已驳回。", "ok"); loadTasks(); }
      }));
    } else actions.append(el("span", "需在专用门禁处理：" + t.decide_endpoint, "muted"));
    row.append(info, actions); desk.append(row);
  }
  section("Video Admission · 一次性 AI 镜头授权");
  const videos = Array.isArray(video.approval_requests) ? video.approval_requests : [];
  if (!videos.length) desk.append(el("div", video.__error ? "读取失败：" + video.__error : "没有视频审批请求。", "empty"));
  for (const v of videos.slice(0, 12)) {
    const row = el("div", "", "row spread glass pad");
    row.append(el("div", `${v.title || "AI 视频创作"} · ${ticketStatus(v.status).label} · 执行 Ticket ${short(v.execution_ticket_id)}（${v.execution_status || "未创建"}）`));
    if (v.can_approve) row.append(button("批准这一次 AI 镜头生成", "qi", async () => {
      const res = await openGate({ title: "Video Admission · 一次性授权", detail: "只批准这一个视频尝试。批准后仍需通过 Video Admission 与 GPU 租约才会启动 Wan。", phrase: "APPROVE-VIDEO-ONCE",
        action: ({ confirmation }) => call(`/video-approval-requests/${v.id}/approve`, { body: { confirmation } }) });
      if (res) { toast("已批准；执行 Ticket " + short(res.execution_ticket_id) + " 排队中，GPU 尚未启动。", "ok"); loadApprovals(); }
    }));
    desk.append(row);
  }
  section("记忆候选 · 入库审批");
  const candidates = Array.isArray(data.memory_candidates) ? data.memory_candidates : [];
  if (!candidates.length) desk.append(el("div", "没有待审批的记忆候选。", "empty"));
  for (const c of candidates) desk.append(candidateRow(c));
  section("自我进化改进提案");
  const proposals = Array.isArray(data.improvement_proposals) ? data.improvement_proposals : [];
  if (!proposals.length) desk.append(el("div", "没有待审查的改进提案。", "empty"));
  for (const p of proposals) {
    const row = el("div", "", "row spread glass pad");
    const link = el("a", "去自我进化实验室审查 »", "btn small"); link.href = "./evolution-lab.html";
    row.append(el("div", (p.title || p.status || "提案") + " · " + (p.status || "")), link); desk.append(row);
  }
}
function candidateRow(c) {
  const row = el("div", "", "row spread glass pad");
  row.append(el("div", `[${c.track === "user" ? "User Track" : "Agent Track"}] ${c.content}（来源 ${c.source} · 置信度 ${c.confidence}）`));
  const actions = el("div", "", "row");
  actions.append(button("批准入库", "qi", async () => {
    const done = await openGate({ title: "记忆入库审批", detail: "批准这条记忆写入 " + (c.track === "user" ? "User Track（用户事实）" : "Agent Track（工作经验）") + "。", phrase: "APPROVE-MEMORY",
      action: ({ confirmation }) => call(`/control/memory/candidates/${c.id}/decide`, { body: { decision: "approve", confirmation } }) });
    if (done) { toast("已入库。", "ok"); state.view === "memory" ? loadMemory() : loadApprovals(); }
  }), button("拒绝", "danger", async () => {
    const done = await openGate({ title: "拒绝记忆候选", detail: "拒绝后该候选不会入库。", phrase: "REJECT-MEMORY", withReason: true,
      action: ({ confirmation, reason }) => call(`/control/memory/candidates/${c.id}/decide`, { body: reason ? { decision: "reject", confirmation, reason } : { decision: "reject", confirmation } }) });
    if (done) { toast("已拒绝。", "ok"); state.view === "memory" ? loadMemory() : loadApprovals(); }
  }));
  row.append(actions);
  return row;
}

// ───────── 智能体 ─────────
async function loadAgents() {
  const data = await soft("/agents");
  const list = Array.isArray(data.agents) ? data.agents : [];
  const roster = rosterFromRegistry(list);
  rows($("agent-table"), roster, [
    [(r) => r.name + " · " + r.cn, "角色"], ["duty", "职责"],
    [(r) => { if (!r.members.length) return badge("未登记 · 派发阻塞", "bad"); const p = agentPresence(r.members[0]); return badge(p.label, { online: "ok", busy: "live" }[p.state] || "warn"); }, "状态"],
    [(r) => { const e = r.members[0]?.effectiveness; return e && e.success_rate !== null ? Math.round(e.success_rate * 100) + "%（" + e.completed + " 次）" : "无历史"; }, "历史成功率"],
    [(r) => { const e = r.members[0]?.effectiveness; return e && e.review_pass_rate !== null ? Math.round(e.review_pass_rate * 100) + "%" : "无审查记录"; }, "独立审查通过率"],
    [(r) => r.members[0]?.current_task_id ? "Ticket " + short(r.members[0].current_task_id) : "—", "当前任务"],
  ]);
  rows($("agent-registry"), list, [
    ["agent_id", "Agent"], ["role", "角色"], ["display_name", "名称"],
    [(a) => { const p = agentPresence(a); return badge(p.label, { online: "ok", busy: "live" }[p.state] || "warn"); }, "心跳"],
    [(a) => when(a.last_seen_at), "最后心跳"], ["source", "来源"],
  ], data.__error ? "读取失败：" + data.__error : "Agent 注册表为空：没有真实在线 Agent 时不会伪造派发。");
}

// ───────── 记忆库 ─────────
async function loadMemory() {
  for (const b of $("mem-tabs").querySelectorAll("button")) b.setAttribute("aria-pressed", String(b.dataset.track === state.track));
  const q = $("mem-q").value.trim();
  const data = await soft(`/control/memory?track=${state.track}&limit=100${q ? "&q=" + encodeURIComponent(q) : ""}`);
  if (data.__error) { toast("记忆读取失败：" + data.__error, "bad"); return; }
  $("mem-semantics").textContent = (data.track_semantics || {})[state.track] + ` · User ${data.counts.user} 条 / Agent ${data.counts.agent} 条 / 待审 ${data.pending_candidates} 条`;
  rows($("mem-table"), data.memories, [
    ["content", "内容"], ["source", "来源"], [(m) => Number(m.confidence).toFixed(2), "置信度"], ["kind", "类型"],
    ["revision", "版本"], [(m) => when(m.updated_at), "更新"],
    [(m) => button("历史", "", () => showHistory(m.id)), "修订"],
  ], "该轨道暂无记忆");
  const [obsidian, approvals] = await Promise.all([soft("/control/memory/obsidian"), soft("/control/approvals")]);
  const box = $("mem-obsidian"); box.replaceChildren();
  box.append(el("p", { not_configured: "未配置 Obsidian Vault（vault/ 目录不存在）。", audited: "已审计：SQLite 为唯一权威源，Vault 为单向只读镜像。", audit_failed: "审计失败：" + (obsidian.error_type || "") }[obsidian.status] || (obsidian.__error || "未知")));
  if (obsidian.counts) { const dl = el("dl", "", "kv"); kv(dl, Object.entries(obsidian.counts)); box.append(dl); }
  const cand = $("mem-candidates"); cand.replaceChildren();
  const list = Array.isArray(approvals.memory_candidates) ? approvals.memory_candidates : [];
  if (!list.length) cand.append(el("div", "没有待审批的记忆候选。", "empty"));
  for (const c of list) cand.append(candidateRow(c));
}
async function showHistory(id) {
  const data = await call(`/control/memory/${id}/history`);
  const box = $("mem-history"); box.replaceChildren();
  box.append(el("p", `记忆 #${id} · ${data.deleted ? "已软删除（历史保留，可据此恢复）" : "当前有效"} · 共 ${data.revisions.length} 个版本`, "muted"));
  let previous = "";
  for (const revision of data.revisions) {
    box.append(el("b", `v${revision.revision} · ${when(revision.changed_at)} · ${revision.source} · ${revision.track}`));
    for (const line of lineDiff(previous, revision.content)) box.append(el("div", (line.op === "add" ? "+ " : line.op === "del" ? "- " : "  ") + line.text, line.op));
    previous = revision.content;
  }
}

// ───────── 知识图谱 ─────────
function buildLegend() {
  const legend = $("kg-legend"); legend.replaceChildren();
  for (const [type, label] of Object.entries(NODE_TYPE_LABELS)) {
    const item = el("label");
    const box = el("input"); box.type = "checkbox"; box.checked = true; box.dataset.type = type;
    box.addEventListener("change", drawGraph);
    item.append(box, el("span", "", "dot dot-" + type), el("span", label + " " + type));
    legend.append(item);
  }
  const select = $("kg-edge");
  for (const [type, label] of Object.entries(EDGE_TYPE_LABELS)) { const o = el("option", label + "（" + type + "）"); o.value = type; select.append(o); }
  select.addEventListener("change", drawGraph);
}
async function loadKnowledge() {
  const q = $("kg-q").value.trim();
  const data = await soft("/knowledge/graph?limit=160" + (q ? "&q=" + encodeURIComponent(q) : ""));
  if (data.__error) { toast("知识图谱读取失败：" + data.__error, "bad"); return; }
  state.graph = data;
  const totals = data.totals || {};
  $("kg-totals").textContent = data.configured ? `活跃节点 ${totals.active_nodes} · 关系 ${totals.active_edges} · 证据 ${totals.claim_evidence_total}` : "Research Wiki 尚未初始化";
  relayoutGraph();
}
function relayoutGraph() {
  if (!state.graph) return;
  const rect = $("kg-canvas").getBoundingClientRect();
  state.graphLayout = layoutGraph(state.graph.nodes || [], state.graph.edges || [], Math.max(300, rect.width), Math.max(300, rect.height));
  drawGraph();
}
function visibleTypes() { return new Set(Array.from($("kg-legend").querySelectorAll("input:checked")).map((i) => i.dataset.type)); }
function drawGraph() {
  const canvas = $("kg-canvas");
  const rect = canvas.getBoundingClientRect();
  const ratio = Math.min(3, window.devicePixelRatio || 1);  // crisp on 4K / 200 % scaling
  canvas.width = Math.floor(rect.width * ratio); canvas.height = Math.floor(rect.height * ratio);
  const ctx = canvas.getContext("2d"); ctx.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.clearRect(0, 0, rect.width, rect.height);
  const types = visibleTypes(); const edgeFilter = $("kg-edge").value;
  const byId = new Map(state.graphLayout.map((n) => [n.id, n]));
  for (const edge of (state.graph && state.graph.edges) || []) {
    if (edgeFilter && edge.edge_type !== edgeFilter) continue;
    const s = byId.get(edge.source_id); const t = byId.get(edge.target_id);
    if (!s || !t || !types.has(s.node_type) || !types.has(t.node_type)) continue;
    ctx.strokeStyle = edge.edge_type === "tested_by" ? "rgba(245,197,99,.9)" : edge.edge_type === "refutes" || edge.edge_type === "contradicts" ? "rgba(248,113,113,.6)" : "rgba(167,139,250,.35)";
    ctx.lineWidth = edge.edge_type === "tested_by" ? 2 : 1;
    ctx.beginPath(); ctx.moveTo(s.x, s.y); ctx.lineTo(t.x, t.y); ctx.stroke();
  }
  for (const node of state.graphLayout) {
    if (!types.has(node.node_type)) continue;
    ctx.fillStyle = NODE_TYPE_COLORS[node.node_type] || "#9C93BF";
    ctx.shadowColor = ctx.fillStyle; ctx.shadowBlur = 10;
    ctx.beginPath(); ctx.arc(node.x, node.y, node.node_type === "claim" ? 6 : 5, 0, Math.PI * 2); ctx.fill();
  }
  ctx.shadowBlur = 0;
  if (!state.graphLayout.length) { ctx.fillStyle = "#9C93BF"; ctx.font = "14px sans-serif"; ctx.fillText("Research Wiki 中还没有节点。可在工具箱用玄学灵感引擎写入假设，或提交研究任务。", 20, 30); }
}
async function onGraphClick(event) {
  const rect = $("kg-canvas").getBoundingClientRect();
  const x = event.clientX - rect.left; const y = event.clientY - rect.top; const types = visibleTypes();
  let best = null; let bestDist = 144;
  for (const node of state.graphLayout) {
    if (!types.has(node.node_type)) continue;
    const d = (node.x - x) ** 2 + (node.y - y) ** 2;
    if (d < bestDist) { best = node; bestDist = d; }
  }
  if (best) await showNode(best.id);
}
async function showNode(id) {
  const data = await call("/knowledge/nodes/" + id);
  const node = data.node || data;
  const box = $("kg-detail"); box.classList.remove("hidden"); box.replaceChildren();
  const meta = node.metadata || {};
  const title = el("h2", node.title || id);
  if (meta.epistemic_display_label === METAPHYSICS_LABEL) title.append(el("span", " " + METAPHYSICS_LABEL, "badge label-meta"));
  box.append(button("关闭（Esc）", "", () => box.classList.add("hidden")), title,
    el("p", `${NODE_TYPE_LABELS[node.node_type] || node.node_type} · 状态 ${node.status || "—"} · ${id}`, "mono"), el("p", node.summary || ""));
  const edges = ((state.graph && state.graph.edges) || []).filter((e) => e.source_id === id || e.target_id === id);
  const titleOf = new Map(state.graphLayout.map((n) => [n.id, n.title]));
  const list = el("ul", "", "feed");
  for (const edge of edges) {
    const other = edge.source_id === id ? edge.target_id : edge.source_id;
    list.append(el("li", `${EDGE_TYPE_LABELS[edge.edge_type] || edge.edge_type}${edge.edge_type === "tested_by" ? "（作品/实验回链）" : ""} → ${titleOf.get(other) || short(other)}`));
  }
  box.append(el("h3", "证据链与关系"), edges.length ? list : el("p", "图谱当前窗口内没有与之相连的关系。", "muted"));
  if (node.node_type === "claim") {
    const chain = await soft(`/control/knowledge/claims/${id}/tested-by`);
    if (!chain.__error) box.append(renderEvidenceChain(chain));
    const dossier = await soft(`/knowledge/claims/${id}/dossier`);
    if (!dossier.__error) { const pre = el("pre", JSON.stringify(dossier.dossier || dossier, null, 2).slice(0, 4000), "mono"); box.append(el("h3", "Claim 证据档案"), pre); }
  }
  const actions = el("div", "", "row");
  actions.append(button("从此节点发起研究任务", "qi", async () => {
    const rid = requestId();
    const res = await openGate({ title: "发起研究 Ticket", detail: "创建一个待审批的研究 Ticket，不会直接执行。", phrase: "SUBMIT-TO-COMMANDER",
      action: ({ confirmation }) => call("/control/tasks", { body: { request_id: rid, title: "研究：" + String(node.title || id).slice(0, 120), goal: `围绕 Research Wiki 节点「${node.title}」开展公开来源研究，必须包含反证检索，并把结论作为证据写回该节点。`, request_kind: "research", source_ref: "research-wiki:" + id, confirmation } }) });
    if (res) toast("已创建待审批研究 Ticket " + short(res.task.id), "ok");
  }));
  if (meta.epistemic_display_label === METAPHYSICS_LABEL && node.node_type === "claim") {
    actions.append(button("提交 Commander 做样片验证", "", async () => {
      const rid = requestId();
      const res = await openGate({ title: "样片验证 Ticket", detail: "为这条玄学推演假设生成待审批的样片验证 Ticket（走正规视频审批链路）。", phrase: "SUBMIT-TO-COMMANDER",
        action: ({ confirmation }) => call("/control/xuanshu/dispatch", { body: { claim_id: id, target: "video", request_id: rid, confirmation } }) });
      if (res) toast("已创建待审批验证 Ticket " + short(res.task.id), "ok");
    }));
  }
  box.append(actions);
  box.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderEvidenceChain(chain) {
  const view = evidenceChainView(chain);
  const wrap = el("div", "", "chain");
  wrap.append(el("h3", "证据链抽屉 · tested_by 闭环"));
  const origin = el("div", "", "origin");
  origin.append(el("b", "原始假设来源"), el("span", " " + view.originLabel, "badge " + (view.originLabel === METAPHYSICS_LABEL ? "label-meta" : "tone-info")),
    el("p", `claim 状态：${view.claimStatus} · ${view.originIsEvidence ? "已标为证据" : "不是证据"}`, "muted"));
  const inspired = (chain.origin && chain.origin.inspired_by) || [];
  if (inspired.length) origin.append(el("p", "灵感来源：" + inspired.map((item) => item.title || short(item.id)).join("、"), "muted"));
  if (chain.origin && chain.origin.test) origin.append(el("p", "检验方法：" + chain.origin.test));
  wrap.append(origin);
  if (!view.records.length) wrap.append(el("p", "尚无实测反馈记录。到“工具箱 → 成片库”针对关联成片提交反馈，即会在此出现 tested_by 回链。", "muted"));
  for (const record of view.records) {
    const item = el("div", "", "record");
    item.append(el("b", "实测反馈记录 · " + record.verdict), el("p", "时间：" + when(record.when) + " · " + (record.independent ? "独立证据" : "单一创作者观察，非独立证据"), "muted"),
      el("p", "成片 SHA-256：" + record.sha, "mono wrap"), el("p", record.feedback));
    wrap.append(item);
  }
  wrap.append(el("small", chain.policy || "", "muted"));
  return wrap;
}

// ───────── 工具箱 ─────────
async function loadToolbox() {
  const readiness = await soft("/control/creation/readiness");
  state.readiness = readiness; state.readinessAt = Date.now();
  const box = $("tb-ready"); box.replaceChildren();
  if (readiness.__error) { box.append(el("div", "就绪度读取失败：" + readiness.__error, "empty")); return; }
  const chosen = { ai_shot_generation: ["当前自动选择：AI 镜头路径", "ok"], local_motion: ["当前自动选择：纯本地动效路径（不调用 Wan / ComfyUI）", "info"], blocked: ["两条路径都未就绪", "bad"] }[readiness.chosen_path];
  box.append(badge(chosen[0], chosen[1]));
  for (const [id, path] of Object.entries(readiness.paths || {})) {
    const line = el("p");
    line.append(el("b", path.label + "："), badge(path.ready ? "就绪" : "缺少 " + path.missing.join("、"), path.ready ? "ok" : "warn"));
    box.append(line);
  }
  box.append(el("small", readiness.note, "muted"));
  const sources = (readiness.paths && readiness.paths.local_motion && readiness.paths.local_motion.video_sources) || [];
  const select = $("tb-source"); const keep = select.value; select.replaceChildren();
  for (const name of sources) { const o = el("option", name); o.value = name; select.append(o); }
  if (!sources.length) { const o = el("option", "（data/video_sources 下没有素材文件夹）"); o.value = ""; select.append(o); }
  if (keep) select.value = keep;
  await loadProducts();
  const links = $("tb-links"); if (!links.childElementCount) {
    for (const [href, label] of [["./xuanshu-lab.html", "玄枢灵感工坊"], ["./research-bridge.html", "深度研究 · 反馈回传"], ["./video-factory.html", "电影级出片规划"], ["./review-desk.html", "待签名审查台"], ["./evolution-lab.html", "自我进化实验室"], ["./consciousness-map.html", "十维意识星图"], ["./", "经典主控台"]]) {
      const a = el("a", label, "btn small"); a.href = href; links.append(a);
    }
  }
}
function resolvedPath() {
  const choice = $("tb-path").value;
  if (choice === "local") return "local_motion";
  if (choice === "ai") return "ai_shot_generation";
  return state.readiness && state.readiness.chosen_path;
}
async function createVideo() {
  const title = $("tb-title").value.trim();
  if (!title) throw new Error("请填写影片名称");
  const path = resolvedPath();
  const progress = $("tb-progress");
  if (path === "blocked" || !path) throw new Error("两条视频路径都未就绪，请先查看上方缺失能力");
  if (path === "ai_shot_generation") {
    const prompt = $("tb-prompt").value.trim();
    if (!prompt) throw new Error("AI 路径需要画面描述");
    const created = await call("/video-approval-requests", { body: { request_id: requestId(), title: title.slice(0, 120), prompt, aspect_ratio: "16:9", duration_seconds: 2, steps: 20, narration_script: $("tb-script").value.trim() } });
    progress.textContent = `实际路径：AI 镜头（Wan / ComfyUI）\nCommander 一次性审批票：${created.approval.task_id}\n状态：${created.approval.status}\n批准前不会启动 Wan 或占用 GPU。请到「任务中心 → 待审批门禁操作台」批准。`;
    return;
  }
  const source = $("tb-source").value; const script = $("tb-script").value.trim();
  if (!source) throw new Error("请选择 data/video_sources 下的素材文件夹");
  if (!script) throw new Error("本地路径需要粤语旁白稿：音频必须来自真实 TTS，不使用占位音");
  const created = await call("/video-products", { body: { mode: "local_assets", source, title: title.slice(0, 160), narration_script: script, motion_profile: $("tb-motion").value, audio_mode: "music" } });
  progress.textContent = "实际路径：纯本地动效（CantoTTS + Whisper ASR + Remotion + FFmpeg），不调用 Wan / ComfyUI。\n任务 " + created.job.id + " 已排队…";
  for (let i = 0; i < 1800; i += 1) {
    await new Promise((resolve) => window.setTimeout(resolve, 2000));
    const job = (await call("/video-jobs/" + created.job.id)).job;
    progress.textContent = `实际路径：纯本地动效\n任务 ${job.id}\n状态：${job.status}${job.error ? "\n错误：" + job.error : ""}`;
    if (job.status === "succeeded") { toast("成片已入库 Product Vault；仍需人工内容/版权审片。", "ok"); await loadProducts(); return; }
    if (job.status === "failed" || job.status === "cancelled") throw new Error(job.error || "视频制作失败（Remotion / TTS / QA 任一失败都会使本次产品失败，不会静默降级）");
    if (job.worker_available === false) throw new Error("渲染进程已不可用（可能 Muse 重启），任务记录已保留，请重新提交。");
  }
}
async function loadClaims(force = false) {
  if (!force && state.claims && Date.now() - state.claimsAt < 30000) return state.claims;
  const data = await soft("/control/knowledge/claims?limit=200");
  state.claims = Array.isArray(data.claims) ? data.claims : []; state.claimsAt = Date.now();
  return state.claims;
}
function draftFor(id) {
  if (!state.drafts.has(id)) state.drafts.set(id, { text: "", decision: "revise", claim: "", verdict: "inconclusive" });
  return state.drafts.get(id);
}
async function loadProducts(force = false) {
  const [data, claims] = await Promise.all([soft("/products?limit=30"), loadClaims(force)]);
  const fingerprint = JSON.stringify([data, claims.map((c) => [c.id, c.tested_by_count])]);
  const box = $("tb-products");
  if (!force && fingerprint === state.lastProducts && box.childElementCount) return;  // keep playing videos and drafts
  if (userIsEditing(box)) return;
  state.lastProducts = fingerprint;
  box.replaceChildren();
  const videos = (Array.isArray(data.products) ? data.products : []).filter((p) => p.kind === "video_creator");
  if (!videos.length) { box.append(el("div", data.__error ? "读取失败：" + data.__error : "成片库为空。用上方表单生成首部真实作品。", "empty")); return; }
  for (const product of videos.slice(0, 6)) {
    const card = el("div", "", "glass pad");
    const video = document.createElement("video"); video.controls = true; video.preload = "metadata"; video.src = product.preview_path || product.video_path;
    const gateInfo = product.quality_gate || {};
    const head = el("div", "", "row spread");
    head.append(el("b", product.name), badge(gateInfo.status === "passed" ? "技术 QA 通过" : gateInfo.status === "failed" ? "技术 QA 未通过" : "QA 未知", gateInfo.status === "passed" ? "ok" : gateInfo.status === "failed" ? "bad" : "warn"));
    card.append(head, video, el("p", "MP4 SHA-256：" + (product.video_mp4_sha256 || "—"), "mono wrap"));
    const zip = el("a", "下载工程 ZIP", "btn small"); zip.href = product.download_path; card.append(zip);
    if (product.creator_feedback_enabled && product.creator_initiative_id) card.append(feedbackForm(product, claims));
    else card.append(el("p", "该作品未关联 Creator Initiative，暂不能提交创作反馈。", "muted"));
    box.append(card);
  }
}
function feedbackForm(product, claims) {
  const draft = draftFor(product.id || product.initiative_id + "-" + product.version_id);
  const form = el("div", "", "feedback-form");
  const text = el("textarea"); text.maxLength = 2000; text.placeholder = "审片意见：哪里好、哪里要改、发现的问题"; text.value = draft.text;
  text.addEventListener("input", () => { draft.text = text.value; });
  const decision = el("select"); decision.setAttribute("aria-label", "创作账本结论");
  for (const [value, label] of [["revise", "需要修改"], ["accept", "验收通过"], ["reject", "不采用"]]) { const o = el("option", label); o.value = value; decision.append(o); }
  decision.value = draft.decision; decision.addEventListener("change", () => { draft.decision = decision.value; });
  const claim = el("select"); claim.setAttribute("aria-label", "关联的待验证假设");
  const none = el("option", "不关联假设（仅写入创作账本）"); none.value = ""; claim.append(none);
  for (const c of claims) {
    const o = el("option", (c.symbolic_hypothesis ? "【玄学推演】" : "【研究】") + c.title.slice(0, 60) + (c.tested_by_count ? "（已有 " + c.tested_by_count + " 条反馈）" : ""));
    o.value = c.id; claim.append(o);
  }
  claim.value = claims.some((c) => c.id === draft.claim) ? draft.claim : "";
  const verdict = el("select"); verdict.setAttribute("aria-label", "这条成片对假设的检验结论");
  for (const [value, label] of [["inconclusive", "无法判断"], ["supports", "支持假设"], ["refutes", "反驳假设"]]) { const o = el("option", label); o.value = value; verdict.append(o); }
  verdict.value = draft.verdict; verdict.disabled = !claim.value;
  verdict.addEventListener("change", () => { draft.verdict = verdict.value; });
  claim.addEventListener("change", () => { draft.claim = claim.value; verdict.disabled = !claim.value; });
  const result = el("small", "", "muted");
  const submit = button("提交反馈", "qi", async () => {
    const feedback = text.value.trim();
    if (!feedback) throw new Error("请先写下审片意见");
    if (claim.value) {
      const rid = draft.requestId || (draft.requestId = requestId());
      const res = await openGate({ title: "反馈直连 Research Wiki", phrase: "LINK-FEEDBACK-TO-CLAIM",
        detail: "写入创作账本，并在 Research Wiki 建立 claim —tested_by→ 实测反馈记录（成片 SHA-256 由服务器从 Product Vault 计算）。不会自动改写 claim 状态。",
        action: ({ confirmation }) => call("/control/creation/feedback", { body: { initiative_id: product.initiative_id, version: product.version_id,
          claim_id: claim.value, verdict: verdict.value, decision: decision.value, feedback, request_id: rid, confirmation } }) });
      if (!res) return;
      result.textContent = (res.replayed ? "已存在（幂等重放）：" : "已回链：") + "记录 " + short(res.feedback_record.id) + " · SHA-256 " + String(res.feedback_record.metadata.video_sha256).slice(0, 16) + "…";
      toast("反馈已写入创作账本，并在知识图谱建立 tested_by 回链。", "ok");
      Object.assign(draft, { text: "", requestId: null }); text.value = "";
      state.claimsAt = 0;
      return;
    }
    await call(`/initiatives/${product.creator_initiative_id}/feedback`, { body: { feedback, decision: decision.value, reviewer: "creator", source: "dashboard" } });
    draft.text = ""; text.value = "";
    toast("反馈已写入创作账本；研究循环会把它转成待核验知识并回链到来源灵感。", "ok");
  });
  form.append(el("h3", "创作者反馈"), decision, text, el("label", "关联假设（可选）", "muted"), claim, verdict, submit, result);
  return form;
}
async function xuanshu(write) {
  const question = $("xs-q").value.trim(); const seed = $("xs-seed").value.trim() || new Date().toISOString().slice(0, 10);
  if (question.length < 2) throw new Error("请输入至少 2 个字的主题");
  const result = write
    ? await openGate({ title: "写入 Research Wiki", phrase: "WRITE-HYPOTHESES",
      detail: "把这些「" + METAPHYSICS_LABEL + "」写入 Research Wiki：作为 proposed 状态的 claim，不会被当作证据。",
      action: ({ confirmation }) => call("/control/xuanshu/hypotheses", { body: { question, seed, write_to_wiki: true, confirmation } }) })
    : await call("/control/xuanshu/hypotheses", { body: { question, seed } });
  if (!result) return;
  state.claimsAt = 0;
  const h = result.hypotheses; const box = $("xs-result"); box.replaceChildren();
  box.append(el("span", h.epistemic_display_label, "badge label-meta"), el("h3", "象征观察"));
  for (const o of h.symbolic_observations) box.append(el("p", "· " + o.text));
  box.append(el("h3", "清晰问题"), el("p", h.clear_question), el("h3", "可检验假设与替代解释"));
  for (const item of h.testable_hypotheses) {
    const card = el("div", "", "glass pad");
    card.append(el("b", item.statement), el("p", "检验：" + item.test), el("p", "成功信号：" + item.success_signal), el("p", "替代解释：" + item.alternative_explanation, "muted"));
    box.append(card);
  }
  if (result.written) {
    box.append(el("p", "已写入 Research Wiki：idea " + short(result.written.idea_id) + "，" + result.written.claims.length + " 条 proposed claim。"));
    for (const claim of result.written.claims) {
      const row = el("div", "", "row");
      row.append(el("span", claim.title), button("提交研究验证", "qi", async () => {
        const rid = requestId();
        const res = await openGate({ title: "提交研究验证", detail: "为该假设创建待审批研究 Ticket（需含反证检索）。", phrase: "SUBMIT-TO-COMMANDER",
          action: ({ confirmation }) => call("/control/xuanshu/dispatch", { body: { claim_id: claim.id, target: "research", request_id: rid, confirmation } }) });
        if (res) toast("已创建待审批 Ticket " + short(res.task.id), "ok");
      }));
      box.append(row);
    }
  }
}

// ───────── 工作流 ─────────
async function loadWorkflow() {
  const [video, attempts, recipes, jobs] = await Promise.all([soft("/video-approval-requests?limit=50"), soft("/video-attempts"), soft("/pipelines/recipes"), soft("/pipelines/jobs?limit=30")]);
  rows($("wf-approvals"), video.approval_requests, [
    ["title", "标题"], [(v) => badge(ticketStatus(v.status).label, ticketStatus(v.status).tone), "审批"],
    [(v) => short(v.attempt_id), "Attempt"], [(v) => short(v.execution_ticket_id), "执行 Ticket"], ["execution_status", "执行状态"], ["approved_by", "批准者"],
  ], video.__error ? "读取失败：" + video.__error : "没有视频审批记录");
  $("wf-attempts").textContent = attempts.__error ? "读取失败：" + attempts.__error : JSON.stringify(attempts, null, 2).slice(0, 6000);
  const recipeList = Array.isArray(recipes.recipes) ? recipes.recipes : [];
  rows($("wf-recipes"), recipeList, [[(r) => r.id || r.recipe_id || r.name, "配方"], [(r) => r.title || r.label || r.description || "", "说明"]], recipes.__error ? "读取失败：" + recipes.__error : "无配方");
  rows($("wf-jobs"), Array.isArray(jobs.jobs) ? jobs.jobs : [], [[(j) => short(j.id || j.job_id), "作业"], ["status", "状态"], [(j) => when(j.updated_at || j.created_at), "时间"]], jobs.__error ? "读取失败：" + jobs.__error : "无作业");
}

// ───────── 设置 ─────────
async function loadSettings() {
  const [daemon, safety, telemetry, gpu, stores, reviewer] = await Promise.all([soft("/daemon/status"), soft("/omega/safety"), soft("/control/telemetry"), soft("/gpu-resources"), soft("/store-health"), soft("/reviewer-key/status")]);
  const d = daemon.daemon || daemon;
  kv($("st-daemon"), daemon.__error ? [["状态", "读取失败：" + daemon.__error]] : [["状态", d.state], ["运行中", d.running ? "是" : "否"], ["心跳延迟", d.heartbeat_age_seconds === null || d.heartbeat_age_seconds === undefined ? "—" : d.heartbeat_age_seconds + " 秒"], ["最近错误", d.last_error || "无"]]);
  const s = safety.status || {};
  kv($("st-safety"), safety.__error ? [["状态", "读取失败：" + safety.__error]] : Object.entries(s).filter(([, v]) => typeof v !== "object").slice(0, 8));
  kv($("st-telemetry"), telemetry.__error ? [["状态", "读取失败：" + telemetry.__error]] : [
    ["逻辑 CPU", telemetry.cpu.logical_cpu_count], ["并发硬上限", telemetry.concurrency.hard_limit], ["CPU 约束上限", telemetry.concurrency.cpu_bound_limit],
    ...resourceBars(telemetry).map((bar) => [bar.label, (bar.value === null ? "" : bar.value + "% · ") + bar.text]),
  ]);
  $("st-gpu").textContent = gpu.__error ? "读取失败：" + gpu.__error : JSON.stringify({ status: gpu.status, lease: gpu.lease, model_supervisor: gpu.model_supervisor, comfyui_memory_release: gpu.comfyui_memory_release, policy: gpu.policy }, null, 2);
  $("st-stores").textContent = stores.__error ? "存储健康：" + stores.__error + "（独立 Status API 未配置存储注册表时如实显示 not_configured）" : JSON.stringify(stores.stores, null, 2);
  $("st-reviewer").textContent = reviewer.__error ? "读取失败：" + reviewer.__error : JSON.stringify(reviewer.key, null, 2);
}
async function safetyControl(action) {
  // The safety endpoint itself needs no phrase; this one is a UI-side guard against mis-clicks only.
  const phrase = action === "pause" ? "PAUSE-AUTONOMY" : "RESUME-AUTONOMY";
  const done = await openGate({ title: action === "pause" ? "暂停 Muse 自治运行" : "恢复 Muse 自治运行", phrase,
    detail: "界面防误触确认（安全控制器接口本身不要求短语）。", action: () => call("/omega/safety/control", { body: { action, reason: "Muse OS 设置页" } }) });
  if (done) { toast("已" + (action === "pause" ? "暂停" : "恢复"), "ok"); loadSettings(); }
}

// ───────── boot ─────────
const BACKGROUND_REFRESH = {
  overview: () => loadOverview(true),
  tasks: () => (userIsEditing($("view-tasks")) ? null : loadTasks(true)),
  workflow: () => loadWorkflow(),
  agents: () => loadAgents(),
  toolbox: () => loadProducts(false),
};
async function pollOnce() {
  try {
    await loadSide();
    if (!gate.open) await BACKGROUND_REFRESH[state.view]?.();
  } catch (error) { /* a failed background refresh is shown by the next foreground action */ }
}
function schedulePoll() {
  window.clearTimeout(state.pollHandle);
  state.pollHandle = window.setTimeout(async () => { await pollOnce(); schedulePoll(); }, pollDelay(15000, document.hidden));
}
function bind() {
  bindGate();
  $("task-refresh").addEventListener("click", () => loadTasks());
  $("task-filter").addEventListener("change", () => loadTasks());
  $("nt-submit").addEventListener("click", () => submitTask().catch((e) => toast(e.message, "bad")));
  for (const b of $("mem-tabs").querySelectorAll("button")) b.addEventListener("click", () => { state.track = b.dataset.track; loadMemory(); });
  $("mem-search").addEventListener("click", loadMemory);
  const memSearch = debounce(loadMemory, 350);
  $("mem-q").addEventListener("input", memSearch);
  $("mem-q").addEventListener("keydown", (event) => { if (event.key === "Enter") loadMemory(); });
  $("kg-load").addEventListener("click", loadKnowledge);
  $("kg-q").addEventListener("keydown", (event) => { if (event.key === "Enter") loadKnowledge(); });
  $("kg-canvas").addEventListener("click", (event) => onGraphClick(event).catch((e) => toast(e.message, "bad")));
  $("tb-create").addEventListener("click", async () => { const b = $("tb-create"); if (b.disabled) return; b.disabled = true; b.setAttribute("aria-busy", "true"); try { await createVideo(); } catch (e) { $("tb-progress").textContent += "\n失败：" + e.message; toast(e.message, "bad"); } finally { b.disabled = false; b.removeAttribute("aria-busy"); } });
  $("xs-preview").addEventListener("click", () => xuanshu(false).catch((e) => toast(e.message, "bad")));
  $("xs-write").addEventListener("click", () => xuanshu(true).catch((e) => toast(e.message, "bad")));
  $("st-pause").addEventListener("click", () => safetyControl("pause").catch((e) => toast(e.message, "bad")));
  $("st-resume").addEventListener("click", () => safetyControl("resume").catch((e) => toast(e.message, "bad")));
  // B391-P2-24: debounced relayout (no refetch per resize event), Esc closes dialog / drawers.
  window.addEventListener("resize", debounce(() => { if (state.view === "knowledge" && state.graph) relayoutGraph(); }, 200));
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (gate.open) { event.preventDefault(); closeGate(null); return; }
    for (const id of ["kg-detail", "task-detail"]) {
      const node = $(id);
      if (!node.classList.contains("hidden") && node.closest(".view.active")) { node.classList.add("hidden"); event.preventDefault(); return; }
    }
  });
  document.addEventListener("visibilitychange", () => { if (!document.hidden) pollOnce(); schedulePoll(); });
}

const starfield = mountStarfield($("starfield"));
buildNav(); buildLegend(); bind(); windowControls();
tick();
const clock = window.setInterval(tick, 1000);
loadSide(); schedulePoll();
go(location.hash.slice(1) || "overview", false);
window.addEventListener("pagehide", () => {
  window.clearInterval(clock); window.clearTimeout(state.pollHandle);
  for (const handle of state.timers) window.clearTimeout(handle);
  if (starfield && typeof starfield.destroy === "function") starfield.destroy();
});
