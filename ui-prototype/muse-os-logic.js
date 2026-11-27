// B391 P2 shared, DOM-free logic for the Portal (/portal) and Super Agent OS (/os).
// Everything here is pure so it can be verified with `node --test`.

export const METAPHYSICS_LABEL = "玄学推演／待验证假设";

export const NODE_TYPE_COLORS = Object.freeze({
  idea: "#F5C563",       // 流光黑金
  claim: "#22D3EE",      // 灵气青
  source: "#A78BFA",     // 星云紫
  experiment: "#34D399", // 翠
  decision: "#F472B6",   // 绯
  failure: "#F87171",    // 赤
});

export const NODE_TYPE_LABELS = Object.freeze({
  idea: "灵感", claim: "断言", source: "来源", experiment: "实验", decision: "决策", failure: "失败",
});

export const EDGE_TYPE_LABELS = Object.freeze({
  supports: "支持", refutes: "反驳", relates: "相关", alternative: "替代解释", tested_by: "被检验",
  addresses_gap: "填补缺口", inspired_by: "灵感来源", contradicts: "矛盾", supersedes: "取代",
});

export const AGENT_ROLES = Object.freeze([
  { id: "commander", name: "Commander", cn: "统帅", duty: "唯一规划、权限、预算、审批与验收中枢" },
  { id: "researcher", name: "Researcher", cn: "研究", duty: "公开来源检索、反证检索与证据整理" },
  { id: "architect", name: "Architect", cn: "架构", duty: "方案设计、DAG 拆解与接口约束" },
  { id: "builder", name: "Builder", cn: "构建", duty: "按批准的计划实现与生成产物" },
  { id: "memory", name: "Memory", cn: "记忆", duty: "双轨记忆读写、修订与镜像审计" },
  { id: "tester", name: "Tester", cn: "测试", duty: "运行测试、QA 与回归验证" },
  { id: "reviewer", name: "Reviewer", cn: "审核", duty: "独立审查与签名证明，不可与 Builder 同一模型" },
]);

export const OS_VIEWS = Object.freeze([
  { id: "overview", label: "总览", icon: "✦" },
  { id: "tasks", label: "任务中心", icon: "☰" },
  { id: "agents", label: "智能体", icon: "❖" },
  { id: "memory", label: "记忆库", icon: "◈" },
  { id: "knowledge", label: "知识图谱", icon: "✺" },
  { id: "toolbox", label: "工具箱", icon: "⚒" },
  { id: "workflow", label: "工作流", icon: "⇄" },
  { id: "settings", label: "设置", icon: "⚙" },
]);

export const ROADMAP = Object.freeze([
  { id: "research", label: "研究深化", detail: "证据独立性审查、反证检索、来源新鲜度" },
  { id: "capability", label: "能力扩展", detail: "更多本地模型、技能融合与插件能力代理" },
  { id: "ecosystem", label: "生态融合", detail: "Ruflo / dsh / Party 适配器在 Commander 授权内协作" },
  { id: "commerce", label: "商业变现", detail: "真实受众验证、单位经济与收入账本（当前 US$0）" },
  { id: "co_creation", label: "文明共创", detail: "跨设备配对、协作审片与公开作品集" },
  { id: "evolution", label: "无限进化", detail: "签名审查门控下的自我改进与回滚" },
]);

/** Format bytes as a short human string; unknown stays unknown. */
export function formatBytes(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "未知";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let index = 0;
  let amount = value;
  while (amount >= 1024 && index < units.length - 1) { amount /= 1024; index += 1; }
  return (index === 0 ? String(amount) : amount.toFixed(amount >= 100 ? 0 : 1)) + " " + units[index];
}

/** Return an integer percentage 0-100 or null when the inputs are not measurable. */
export function percent(part, total) {
  if (typeof part !== "number" || typeof total !== "number" || !Number.isFinite(part)
      || !Number.isFinite(total) || total <= 0 || part < 0) return null;
  return Math.max(0, Math.min(100, Math.round((part / total) * 100)));
}

/** Queue success rate from Ticket counts; null when nothing finished yet. */
export function successRate(counts) {
  if (!counts || typeof counts !== "object") return null;
  const ok = Number(counts.succeeded) || 0;
  const failed = Number(counts.failed) || 0;
  return ok + failed > 0 ? Math.round((ok / (ok + failed)) * 1000) / 10 : null;
}

/** Memory / storage utilisation from telemetry. */
export function resourceBars(telemetry) {
  const memory = telemetry && telemetry.memory ? telemetry.memory : {};
  const storage = telemetry && telemetry.storage ? telemetry.storage : {};
  const usedMemory = typeof memory.total_bytes === "number" && typeof memory.available_bytes === "number"
    ? memory.total_bytes - memory.available_bytes : null;
  return [
    { id: "cpu", label: "CPU 线程", value: null,
      text: telemetry && telemetry.cpu && telemetry.cpu.logical_cpu_count
        ? telemetry.cpu.logical_cpu_count + " 线程（占用率未测量）" : "未知" },
    { id: "memory", label: "内存", value: percent(usedMemory, memory.total_bytes),
      text: usedMemory === null ? "未知" : formatBytes(usedMemory) + " / " + formatBytes(memory.total_bytes) },
    { id: "storage", label: "存储", value: percent(storage.used_bytes, storage.total_bytes),
      text: typeof storage.used_bytes === "number" ? formatBytes(storage.free_bytes) + " 可用" : "未知" },
    { id: "network", label: "网络", value: null, text: "未测量（不猜测）" },
  ];
}

/**
 * Health checks for the Portal "当前进度" panel. Each check is derived from a real API result.
 * Returns { checks, passed, total, percent }.
 */
export function healthChecks({ health, summary, readiness, signedReviewer } = {}) {
  const checks = [
    { id: "api", label: "Status API 在线", ok: !!health && health.status === "ok" },
    { id: "queue", label: "Commander 任务队列可读", ok: !!summary && !!summary.queue },
    { id: "wiki", label: "研究知识库 Research Wiki", ok: !!summary && summary.knowledge && summary.knowledge.status === "available" },
    { id: "memory", label: "双轨记忆库", ok: !!summary && summary.memory && summary.memory.status === "available" },
    { id: "local_video", label: "本地动效成片路径（TTS+ASR+Remotion+FFmpeg）",
      ok: !!readiness && readiness.paths && readiness.paths.local_motion && readiness.paths.local_motion.ready === true },
    { id: "ai_video", label: "AI 镜头路径（Wan/ComfyUI + GPU）",
      ok: !!readiness && readiness.paths && readiness.paths.ai_shot_generation && readiness.paths.ai_shot_generation.ready === true },
    { id: "product", label: "已有真实成片入库",
      ok: !!summary && summary.products && Number(summary.products.videos) > 0 },
    { id: "reviewer", label: "独立 Reviewer 签名密钥", ok: signedReviewer === true },
  ];
  const passed = checks.filter((item) => item.ok).length;
  return { checks, passed, total: checks.length, percent: Math.round((passed / checks.length) * 100) };
}

/** Line-level diff for memory revision history (LCS based, bounded). */
export function lineDiff(before, after) {
  // B391-P2-20: an empty text has zero lines (the first revision is all additions, no phantom deletion).
  const lines = (value) => { const text = String(value ?? ""); return text === "" ? [] : text.split("\n").slice(0, 400); };
  const a = lines(before);
  const b = lines(after);
  const table = Array.from({ length: a.length + 1 }, () => new Array(b.length + 1).fill(0));
  for (let i = a.length - 1; i >= 0; i -= 1) {
    for (let j = b.length - 1; j >= 0; j -= 1) {
      table[i][j] = a[i] === b[j] ? table[i + 1][j + 1] + 1 : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  const out = [];
  let i = 0; let j = 0;
  while (i < a.length && j < b.length) {
    if (a[i] === b[j]) { out.push({ op: "same", text: a[i] }); i += 1; j += 1; }
    else if (table[i + 1][j] >= table[i][j + 1]) { out.push({ op: "del", text: a[i] }); i += 1; }
    else { out.push({ op: "add", text: b[j] }); j += 1; }
  }
  while (i < a.length) { out.push({ op: "del", text: a[i] }); i += 1; }
  while (j < b.length) { out.push({ op: "add", text: b[j] }); j += 1; }
  return out;
}

/** Map a Ticket status to a Chinese label and tone. */
export function ticketStatus(status) {
  const map = {
    queued: ["排队中", "info"], running: ["执行中", "live"], waiting_approval: ["待审批", "warn"],
    succeeded: ["已完成", "ok"], failed: ["失败", "bad"], cancelled: ["已取消", "muted"],
  };
  const [label, tone] = map[status] || [String(status || "未知"), "muted"];
  return { label, tone };
}

/** Agent online state from the registry row; never invents a live Agent. */
export function agentPresence(agent, nowMs = Date.now()) {
  if (!agent || agent.enabled === false) return { state: "disabled", label: "已停用" };
  const seen = agent.last_seen_at ? Date.parse(agent.last_seen_at) : NaN;
  const staleAfter = Number(agent.stale_after_seconds) || 0;
  if (!Number.isFinite(seen)) return { state: "never_seen", label: "从未上线" };
  if (staleAfter > 0 && nowMs - seen > staleAfter * 1000) return { state: "stale", label: "心跳过期" };
  if (agent.current_task_id) return { state: "busy", label: "执行中" };
  return { state: "online", label: "在线" };
}

/** Match registry Agents to the seven canonical roles (case-insensitive role / agent_id). */
export function rosterFromRegistry(agents) {
  const list = Array.isArray(agents) ? agents : [];
  return AGENT_ROLES.map((role) => {
    const members = list.filter((agent) => {
      const text = (String(agent.role || "") + " " + String(agent.agent_id || "")).toLowerCase();
      return text.includes(role.id);
    });
    return { ...role, members };
  });
}

/** Build a 32-hex request id from a crypto source (injectable for tests). */
export function requestId(randomBytes) {
  const bytes = randomBytes || globalThis.crypto.getRandomValues(new Uint8Array(16));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}

/** Deterministic circular layout + light force relaxation for the knowledge graph. */
export function layoutGraph(nodes, edges, width, height, iterations = 80) {
  const items = (Array.isArray(nodes) ? nodes : []).slice(0, 250);
  const index = new Map(items.map((node, i) => [node.id, i]));
  const cx = width / 2; const cy = height / 2; const radius = Math.min(width, height) * 0.38;
  const pos = items.map((node, i) => {
    const angle = (i / Math.max(1, items.length)) * Math.PI * 2;
    return { x: cx + Math.cos(angle) * radius, y: cy + Math.sin(angle) * radius };
  });
  const links = (Array.isArray(edges) ? edges : [])
    .map((edge) => [index.get(edge.source_id), index.get(edge.target_id)])
    .filter(([s, t]) => s !== undefined && t !== undefined);
  for (let step = 0; step < iterations; step += 1) {
    const forces = pos.map(() => ({ x: 0, y: 0 }));
    for (let i = 0; i < pos.length; i += 1) {
      for (let j = i + 1; j < pos.length; j += 1) {
        const dx = pos[i].x - pos[j].x; const dy = pos[i].y - pos[j].y;
        const dist2 = Math.max(64, dx * dx + dy * dy);
        const push = 1800 / dist2;
        forces[i].x += dx * push; forces[i].y += dy * push;
        forces[j].x -= dx * push; forces[j].y -= dy * push;
      }
    }
    for (const [s, t] of links) {
      const dx = pos[t].x - pos[s].x; const dy = pos[t].y - pos[s].y;
      forces[s].x += dx * 0.02; forces[s].y += dy * 0.02;
      forces[t].x -= dx * 0.02; forces[t].y -= dy * 0.02;
    }
    for (let i = 0; i < pos.length; i += 1) {
      pos[i].x = Math.max(16, Math.min(width - 16, pos[i].x + Math.max(-12, Math.min(12, forces[i].x + (cx - pos[i].x) * 0.004))));
      pos[i].y = Math.max(16, Math.min(height - 16, pos[i].y + Math.max(-12, Math.min(12, forces[i].y + (cy - pos[i].y) * 0.004))));
    }
  }
  return items.map((node, i) => ({ ...node, x: pos[i].x, y: pos[i].y }));
}

/** True when an object carries the mandatory metaphysics label. */
export function hasMetaphysicsLabel(payload) {
  return !!payload && payload.epistemic_display_label === METAPHYSICS_LABEL && payload.is_evidence === false;
}

// ───────── B391-P2 interaction helpers (DOM-free, unit-tested) ─────────

/** Live spelling check for a confirmation phrase: exact match only, but shows how far the user got. */
export function phraseProgress(input, phrase) {
  const typed = String(input ?? "");
  const target = String(phrase ?? "");
  let prefix = 0;
  while (prefix < typed.length && prefix < target.length && typed[prefix] === target[prefix]) prefix += 1;
  return {
    ok: target.length > 0 && typed === target,
    prefix,
    state: typed === "" ? "empty" : typed === target ? "match" : prefix === typed.length ? "partial" : "mismatch",
  };
}

/** Polling cadence: slower while the page is hidden, never faster than 5 s. */
export function pollDelay(baseMs, hidden) {
  const base = Number.isFinite(baseMs) && baseMs >= 5000 ? baseMs : 5000;
  return hidden ? Math.max(base * 4, 60000) : base;
}

/** Human-readable error with the backend code kept visible (e.g. “HTTP 409 · …”). */
export function describeApiError(status, body, fallback) {
  const code = Number.isInteger(status) && status > 0 ? "HTTP " + status : "网络错误";
  const hints = { 400: "请求参数不合法", 401: "未登录或会话已失效", 403: "来源或权限被拒绝", 404: "资源或接口不存在",
    405: "方法不被允许", 409: "被安全门禁拒绝或状态冲突", 413: "请求体过大", 415: "请求格式不正确", 416: "请求范围无效",
    500: "服务器内部错误", 502: "本地网关无法连接引擎", 503: "依赖服务暂不可用" };
  const detail = body && typeof body.error === "string" && body.error.trim() ? body.error.trim() : (fallback || hints[status] || "请求失败");
  const hint = hints[status] && detail !== hints[status] ? "（" + hints[status] + "）" : "";
  return code + " · " + detail.slice(0, 300) + hint;
}

/** Stable keyed merge: keeps object identity for unchanged rows so the DOM can be patched in place. */
export function rowsChanged(previous, next, key = "id") {
  if (!Array.isArray(previous) || !Array.isArray(next) || previous.length !== next.length) return true;
  for (let i = 0; i < next.length; i += 1) {
    const a = previous[i]; const b = next[i];
    if (!a || !b || a[key] !== b[key] || JSON.stringify(a) !== JSON.stringify(b)) return true;
  }
  return false;
}

/** Splits a claim's evidence chain into the symbolic origin and empirical feedback records. */
export function evidenceChainView(chain) {
  const origin = (chain && chain.origin) || {};
  const records = Array.isArray(chain && chain.feedback_records) ? chain.feedback_records : [];
  return {
    originLabel: origin.epistemic_display_label || (origin.kind === "symbolic_hypothesis" ? METAPHYSICS_LABEL : "研究假设"),
    originIsEvidence: origin.is_evidence === true,
    claimStatus: (chain && chain.claim_status) || "未知",
    records: records.map((r) => ({
      id: r.id,
      when: r.recorded_at || "未知时间",
      verdict: { supports: "支持假设", refutes: "反驳假设", inconclusive: "无法判断" }[r.creator_verdict] || "未记录",
      sha: typeof r.video_sha256 === "string" && /^[0-9a-f]{64}$/.test(r.video_sha256) ? r.video_sha256 : "未知",
      independent: r.independent_evidence === true,
      feedback: String(r.feedback || ""),
    })),
  };
}
