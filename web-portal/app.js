const $ = (id) => document.getElementById(id);
let csrfToken = "";
let loading = false;
async function api(path, options = {}) {
  if (options.rawBody !== undefined && options.body !== undefined) throw new TypeError("Choose body or rawBody, not both.");
  if (options.rawBody !== undefined && typeof options.rawBody !== "string") throw new TypeError("rawBody must be a string.");
  const headers = { Accept: "application/json", ...(options.headers || {}) };
  const hasBody = options.body !== undefined || options.rawBody !== undefined;
  if (hasBody) headers["Content-Type"] = "application/json";
  if (options.csrf) headers["X-Muse-CSRF"] = csrfToken;
  const response = await fetch(path, {
    method: options.method || "GET", headers,
    body: options.rawBody !== undefined ? options.rawBody : (options.body === undefined ? undefined : JSON.stringify(options.body)),
    credentials: "same-origin", cache: "no-store", redirect: "error",
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "请求失败：" + response.status);
  return payload;
}
function showLogin(message = "") {
  $("login-view").classList.remove("hidden");
  $("dashboard").classList.add("hidden");
  $("logout").classList.add("hidden");
  $("login-error").textContent = message;
  $("password").value = "";
}
function showDashboard() {
  $("login-view").classList.add("hidden");
  $("dashboard").classList.remove("hidden");
  $("logout").classList.remove("hidden");
}
function number(value) { return Number.isFinite(Number(value)) ? Number(value).toLocaleString() : "—"; }
function escapeHtml(value) { return String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
async function loadDashboard() {
  if (loading) return;
  loading = true;
  $("connection-state").textContent = "SYNCING";
  try {
    const [overview, agents, inspirations, knowledge, plugins, tasks] = await Promise.all([
      api("/api/v1/portal/overview"), api("/api/v1/portal/agents"),
      api("/api/v1/portal/inspirations"), api("/api/v1/portal/knowledge"),
      api("/api/v1/portal/plugins"),
      api("/api/v1/portal/tasks").catch((error) => ({ tasks: [], error: error.message })),
    ]);
    const counts = overview.queue?.counts || {};
    $("queued").textContent = number(counts.queued);
    $("running").textContent = number(counts.running);
    $("approval").textContent = number(counts.waiting_approval);
    $("agents").textContent = number((agents.agents || []).filter((a) => a.status === "online" || a.status === "busy").length);
    const events = overview.recent_events || [];
    $("events").innerHTML = events.length ? events.slice(0, 8).map((e) => `<article class="event"><i></i><div><b>${escapeHtml(e.summary || e.event || e.category || "系统事件")}</b><small>${escapeHtml(e.created_at || e.timestamp || "")}</small></div></article>`).join("") : '<p class="empty">暂无近期事件</p>';
    const taskItems = tasks.tasks || [];
    const taskStatus = { queued: "排队中", running: "执行中", waiting_approval: "待审批", succeeded: "已完成", failed: "失败待检查", cancelled: "已取消" };
    $("task-list").innerHTML = tasks.error
      ? `<p class="empty">任务账本暂不可用：${escapeHtml(tasks.error)}</p>`
      : (taskItems.map((task) => `<article class="task"><div><b>${escapeHtml(task.title || task.id)}</b><small>${escapeHtml(task.id)} · ${escapeHtml(task.workload_class || "general")} · ${escapeHtml(task.updated_at || task.created_at || "")}</small></div><span class="status">${escapeHtml(taskStatus[task.status] || task.status || "未知")}</span></article>`).join("") || '<p class="empty">Commander 任务队列暂时为空</p>');
    $("agent-list").innerHTML = (agents.agents || []).slice(0, 8).map((a) => `<article class="agent"><div><b>${escapeHtml(a.display_name || a.agent_id)}</b><small>${escapeHtml(a.role || a.title || "Agent")}</small></div><span class="status">${escapeHtml(a.status || "unknown")}</span></article>`).join("") || '<p class="empty">尚无已登记 Agent</p>';
    const inspirationItems = inspirations.inspirations || [];
    $("inspirations").innerHTML = inspirationItems.map((i) => {
      const inspirationId = String(i.id || "");
      const canEvolve = /^[0-9a-f]{32}$/.test(inspirationId);
      const external = i.allow_external_research === true;
      return `<article class="inspiration">
        <b>${escapeHtml(i.title || i.content || inspirationId || "灵感记录")}</b>
        <small>${escapeHtml(i.status || i.created_at || "")} · ${external ? "允许外部检索" : "仅本地研究"} · ${escapeHtml(i.evolution_stage || "not_evolved")}</small>
        <div class="inspiration-actions">
          <span class="muted">${escapeHtml(i.epistemic_status || "待验证假设")}</span>
          <button class="quiet inspiration-evolve" type="button" data-inspiration-id="${escapeHtml(inspirationId)}" ${canEvolve ? "" : "disabled"}>启动演化闭环</button>
        </div>
      </article>`;
    }).join("") || '<p class="empty">暂无灵感记录</p>';
    const totals = knowledge.totals || {};
    const nodes = knowledge.nodes || [];
    const summary = '<div class="event"><i></i><div><b>' + number(totals.active_nodes) +
      ' 个知识节点 · ' + number(totals.active_edges) + ' 条关联</b><small>' +
      number(totals.claim_evidence_total) + ' 条主张证据</small></div></div>';
    const nodeCards = nodes.slice(0, 5).map((node) =>
      '<article class="inspiration"><b>' + escapeHtml(node.title || node.id) +
      '</b><small>' + escapeHtml(node.node_type || "node") + ' · ' +
      escapeHtml(node.status || "") + ' · ' +
      escapeHtml(node.epistemic_status || "待评估") + '</small></article>'
    ).join("");
    $("knowledge").innerHTML = summary + (nodeCards || '<p class="empty">知识库暂未建立节点</p>');
    $("plugins").innerHTML = (plugins.plugins || []).map((p) => {
      const capabilities = Array.isArray(p.manifest?.capabilities) ? p.manifest.capabilities : [];
      const requestForm = p.status === "enabled" && capabilities.length ?
        `<form class="plugin-request-form" data-plugin-id="${escapeHtml(p.plugin_id)}">
          <label>请求能力</label>
          <select name="capability" required>${capabilities.map((capability) => `<option value="${escapeHtml(capability.id)}">${escapeHtml(capability.id)}</option>`).join("")}</select>
          <label>请求参数 JSON</label>
          <textarea name="payload" rows="3" spellcheck="false" required>{}</textarea>
          <button class="quiet" type="submit">提交至 Commander 审批</button>
          <p class="form-result" role="status" aria-live="polite"></p>
        </form>` : '<p class="muted plugin-gate">须先由本机管理员独立审批并启用；网页不会替插件授权或激活。</p>';
      return `<article class="plugin"><b>${escapeHtml(p.manifest?.name || p.plugin_id)}</b>
        <small>${escapeHtml(p.plugin_id)} · v${escapeHtml(p.version)}</small>
        <p class="muted">${escapeHtml(p.manifest?.description || "")}</p>
        <span class="status">${escapeHtml(String(p.status || "unknown").toUpperCase())}</span>${requestForm}</article>`;
    }).join("") || '<p class="empty">插件注册表已就绪，等待待审核候选</p>';
    $("updated").textContent = "同步时间 " + new Date().toLocaleString();
    $("connection-state").textContent = "SECURE · ONLINE";
  } catch (error) {
    $("connection-state").textContent = "CONNECTION DEGRADED";
    $("events").innerHTML = '<p class="empty">' + escapeHtml(error.message) + "</p>";
  } finally { loading = false; }
}
async function restoreSession() {
  try {
    const session = await api("/api/v1/portal/session");
    csrfToken = session.csrf_token;
    showDashboard();
    await loadDashboard();
  } catch { showLogin(); }
}
$("inspiration-create-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = $("inspiration-create-form");
  const button = form.querySelector("button");
  const status = $("inspiration-submit-result");
  const content = $("inspiration-content").value.trim();
  const title = $("inspiration-title").value.trim();
  const allowExternal = $("inspiration-external-research").value === "true";
  button.disabled = true;
  status.textContent = "正在写入灵感池…";
  try {
    const payload = { content, allow_external_research: allowExternal, source: "user_manual" };
    if (title) payload.title = title;
    const result = await api("/api/v1/portal/inspirations", {
      method: "POST", csrf: true, body: payload,
    });
    status.textContent = `已收录：${result.inspiration?.title || title || "新灵感"}；${allowExternal ? "已明确允许外部检索" : "保持本地研究"}。`;
    form.reset();
    await loadDashboard();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$("inspirations").addEventListener("click", async (event) => {
  const button = event.target.closest?.(".inspiration-evolve");
  if (!button || button.disabled) return;
  const inspirationId = button.dataset.inspirationId;
  const status = $("inspiration-workflow-result");
  button.disabled = true;
  status.textContent = "演化中：整理本地上下文、研究证据与反证、生成改进提案和蓝图；不会自动执行代码或启动视频任务…";
  try {
    const result = await api("/api/v1/portal/inspirations/evolve", {
      method: "POST", csrf: true, body: { inspiration_id: inspirationId },
    });
    const evolution = result.evolution || {};
    const proposalCount = Array.isArray(evolution.improvement_proposal_ids) ? evolution.improvement_proposal_ids.length : 0;
    status.textContent = `闭环完成：${evolution.external_network_called ? "已按该灵感的授权联网研究" : "仅使用本地研究"}；${proposalCount} 项改进提案；蓝图：${evolution.blueprint_path || "已生成"}。代码修改仍需独立人工审批。`;
    await loadDashboard();
  } catch (error) {
    status.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});
$("login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("login-error").textContent = "";
  const button = $("login-form").querySelector("button");
  button.disabled = true;
  try {
    const result = await api("/api/v1/portal/login", { method: "POST", body: { password: $("password").value } });
    csrfToken = result.csrf_token;
    showDashboard();
    await loadDashboard();
  } catch (error) { $("login-error").textContent = error.message; }
  finally { button.disabled = false; }
});
$("refresh").addEventListener("click", loadDashboard);
$("logout").addEventListener("click", async () => {
  try { await api("/api/v1/portal/logout", { method: "POST", csrf: true, body: {} }); } catch {}
  csrfToken = "";
  showLogin();
});
$("plugin-discover-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const form = $("plugin-discover-form");
  const button = form.querySelector("button");
  const status = $("plugin-submit-result");
  button.disabled = true;
  status.textContent = "正在验证并登记清单…";
  try {
    const manifestText = $("plugin-manifest").value.trim();
    const manifest = JSON.parse(manifestText);
    if (!manifest || typeof manifest !== "object" || Array.isArray(manifest)) throw new Error("Manifest 必须是 JSON 对象。");
    const result = await api("/api/v1/portal/plugins/discover", {
      method: "POST", csrf: true, rawBody: '{"manifest":' + manifestText + '}',
    });
    status.textContent = `已登记 ${result.plugin.plugin_id} v${result.plugin.version}；状态：${result.plugin.status}。仍需独立审核，未执行任何插件代码。`;
    await loadDashboard();
  } catch (error) {
    status.textContent = error instanceof SyntaxError ? "Manifest JSON 格式无效。" : error.message;
  } finally {
    button.disabled = false;
  }
});
$("plugins").addEventListener("submit", async (event) => {
  const form = event.target.closest(".plugin-request-form");
  if (!form) return;
  event.preventDefault();
  const button = form.querySelector("button");
  const status = form.querySelector(".form-result");
  button.disabled = true;
  status.textContent = "正在提交 Commander 审批…";
  try {
    const payloadText = form.elements.namedItem("payload").value.trim();
    const payload = JSON.parse(payloadText);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("请求参数必须是 JSON 对象。");
    }
    const pluginId = encodeURIComponent(form.dataset.pluginId);
    const capabilityId = encodeURIComponent(form.elements.namedItem("capability").value);
    const result = await api(`/api/v1/portal/plugins/${pluginId}/capabilities/${capabilityId}/request`, {
      method: "POST", csrf: true, rawBody: '{"payload":' + payloadText + '}',
    });
    const confirmation = `已进入 Commander 待审批队列：${result.commander_result?.task_id || "已受理"}。不会自动执行。`;
    await loadDashboard();
    const refreshedForm = Array.from(document.querySelectorAll(".plugin-request-form")).find((item) =>
      item.dataset.pluginId === form.dataset.pluginId && item.elements.namedItem("capability").value === form.elements.namedItem("capability").value
    );
    if (refreshedForm) refreshedForm.querySelector(".form-result").textContent = confirmation;
  } catch (error) {
    status.textContent = error instanceof SyntaxError ? "请求参数 JSON 格式无效。" : error.message;
  } finally {
    button.disabled = false;
  }
});
restoreSession();
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
