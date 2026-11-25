// Muse signed review desk. All ticket data is untrusted: rendered with textContent only.
const API = "/api/v1";
const $ = (id) => document.getElementById(id);
let current = null;
let busy = false;

function toast(message, kind = "info") {
  const node = $("toast");
  node.textContent = message;
  node.className = "toast " + kind;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.add("hidden"), 6000);
}

async function call(path, options = {}) {
  const response = await fetch(API + path, {
    credentials: "same-origin",
    headers: { Accept: "application/json", ...(options.body ? { "Content-Type": "application/json" } : {}) },
    ...options,
  });
  let data = {};
  try { data = await response.json(); } catch { data = { error: "服务器返回了非 JSON 内容" }; }
  if (!response.ok) throw new Error(data.error || ("请求失败：" + response.status));
  return data;
}

function renderKey(key) {
  const status = $("key-status");
  if (key && key.initialized && key.trust_store_ok) {
    status.textContent = "已就绪 · 密钥 " + key.key_id + " · 指纹 " + String(key.fingerprint_sha256).slice(0, 16) + "…";
    status.className = "ok";
    $("init-form").classList.add("hidden");
  } else if (key && key.initialized) {
    status.textContent = "密钥文件存在但信任库校验失败：" + (key.error || "未知错误");
    status.className = "bad";
  } else {
    status.textContent = "还没有审查密钥。请先在下面一键生成。";
    status.className = "warn";
    $("init-form").classList.remove("hidden");
  }
}

function renderQueue(reviews) {
  const list = $("queue");
  list.replaceChildren();
  $("queue-empty").classList.toggle("hidden", reviews.length > 0);
  for (const item of reviews) {
    const row = document.createElement("li");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "queue-item";
    const title = document.createElement("strong");
    title.textContent = item.title || item.task_id;
    const meta = document.createElement("span");
    meta.textContent = (item.kind || "任务") + " · " + (item.requested_at || "") + (item.hashes_match ? "" : " · ⚠ 完整性异常");
    button.append(title, meta);
    button.addEventListener("click", () => openDetail(item.task_id));
    row.append(button);
    list.append(row);
  }
}

async function refresh() {
  try {
    const data = await call("/signed-reviews");
    renderKey(data.key);
    renderQueue(data.reviews || []);
  } catch (error) {
    toast("读取失败：" + error.message, "bad");
  }
}

async function openDetail(taskId) {
  try {
    const data = await call("/signed-reviews/" + encodeURIComponent(taskId));
    current = data.review;
    $("d-title").textContent = current.title;
    $("d-kind").textContent = current.kind || "";
    $("d-artifact").textContent = (current.binding || {}).artifact_sha256 || "";
    $("d-evidence").textContent = (current.binding || {}).test_evidence_sha256 || "";
    $("d-integrity").textContent = current.hashes_match ? "✔ 内容与停放时的指纹一致" : "✖ 内容已被改动，禁止签名";
    $("d-integrity").className = current.hashes_match ? "ok" : "bad";
    $("d-artifact-preview").textContent = current.artifact_preview + (current.artifact_truncated ? "\n…（已截断，共 " + current.artifact_bytes + " 字节）" : "");
    $("d-evidence-preview").textContent = current.test_evidence_preview + (current.test_evidence_truncated ? "\n…（已截断）" : "");
    $("detail").classList.remove("hidden");
    $("pass").value = "";
    $("detail").scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
  } catch (error) {
    toast("打开失败：" + error.message, "bad");
  }
}

$("decide-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!current || busy) return;
  const verdict = event.submitter && event.submitter.dataset.verdict;
  if (verdict !== "approve" && verdict !== "reject") return;
  const label = verdict === "approve" ? "签名通过" : "签名拒绝";
  if (!window.confirm("确认对「" + current.title + "」" + label + "？签名后不可撤回。")) return;
  busy = true;
  try {
    const data = await call("/signed-reviews/" + encodeURIComponent(current.task_id) + "/decide", {
      method: "POST",
      body: JSON.stringify({ verdict, passphrase: $("pass").value, note: $("note").value, confirmation: "SIGN-REVIEW" }),
    });
    toast("已" + label + "：票据状态 = " + data.result.status, "ok");
    $("detail").classList.add("hidden");
    current = null;
    await refresh();
  } catch (error) {
    toast(label + "失败：" + error.message, "bad");
  } finally {
    $("pass").value = "";
    busy = false;
  }
});

$("init-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (busy) return;
  busy = true;
  try {
    const data = await call("/reviewer-key/initialize", {
      method: "POST",
      body: JSON.stringify({ passphrase: $("init-pass").value, confirm_passphrase: $("init-pass2").value, confirmation: "INIT-REVIEWER-KEY" }),
    });
    renderKey(data.key);
    toast("审查密钥已生成。请记住口令——忘记后只能重新生成密钥。", "ok");
  } catch (error) {
    toast("生成失败：" + error.message, "bad");
  } finally {
    $("init-pass").value = "";
    $("init-pass2").value = "";
    busy = false;
  }
});

$("refresh").addEventListener("click", refresh);
refresh();
