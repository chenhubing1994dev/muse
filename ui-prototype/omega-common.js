// Shared helpers for Muse Omega pages. All server data is untrusted: textContent only, never HTML string injection.
export const API = "/api/v1";
export const $ = (id) => {
  const node = document.getElementById(id);
  if (!node) throw new Error("missing element #" + id);
  return node;
};

export function el(tag, text = "", className = "") {
  const node = document.createElement(tag);
  if (text !== "" && text !== null && text !== undefined) node.textContent = String(text);
  if (className) node.className = className;
  return node;
}

export function toast(message, kind = "info") {
  const node = $("toast");
  node.textContent = message;
  node.className = "toast " + kind;
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.add("hidden"), 6000);
}

const HTTP_HINTS = { 400: "请求参数不合法", 401: "未登录或会话已失效", 403: "来源或权限被拒绝", 404: "资源或接口不存在",
  405: "方法不被允许", 409: "被安全门禁拒绝或状态冲突", 413: "请求体过大", 415: "请求格式不正确", 416: "请求范围无效",
  500: "服务器内部错误", 502: "本地网关无法连接引擎", 503: "依赖服务暂不可用" };

/** Error raised by call(): keeps the HTTP status and the backend error text (B391-P2-21). */
export class ApiError extends Error {
  constructor(status, detail) {
    const code = status ? "HTTP " + status : "网络错误";
    const hint = HTTP_HINTS[status] && detail !== HTTP_HINTS[status] ? "（" + HTTP_HINTS[status] + "）" : "";
    super(code + " · " + String(detail || HTTP_HINTS[status] || "请求失败").slice(0, 300) + hint);
    this.name = "ApiError";
    this.status = status || 0;
    this.detail = detail || "";
  }
}

export async function call(path, options = {}) {
  const init = { credentials: "same-origin", headers: { Accept: "application/json" }, method: options.method || "GET" };
  if (options.body !== undefined) {
    init.method = options.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const controller = typeof AbortController === "function" ? new AbortController() : null;
  const timeoutMs = Number.isFinite(options.timeoutMs) ? options.timeoutMs : (options.body !== undefined ? 60000 : 30000);
  const timer = controller ? window.setTimeout(() => controller.abort(), timeoutMs) : null;
  if (controller) init.signal = controller.signal;
  let response;
  try {
    response = await fetch(API + path, init);
  } catch (error) {
    const aborted = error && error.name === "AbortError";
    throw new ApiError(0, aborted ? "请求超时（" + Math.round(timeoutMs / 1000) + " 秒），本机引擎可能繁忙或未启动" : "无法连接本机 Muse 引擎：" + (error && error.message || "未知错误"));
  } finally {
    if (timer) window.clearTimeout(timer);
  }
  let data = {};
  try { data = await response.json(); } catch { data = { error: "服务器返回了非 JSON 内容" }; }
  if (!response.ok) throw new ApiError(response.status, data && typeof data.error === "string" ? data.error : "");
  return data;
}

export async function readFile(input) {
  const file = input.files && input.files[0];
  if (!file) throw new Error("请先选择文件");
  if (file.size > 8 * 1024 * 1024) throw new Error("文件超过 8 MiB");
  const buffer = await file.arrayBuffer();
  const digest = await crypto.subtle.digest("SHA-256", buffer);
  const sha256 = Array.from(new Uint8Array(digest)).map((b) => b.toString(16).padStart(2, "0")).join("");
  let binary = "";
  const bytes = new Uint8Array(buffer);
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  return { name: file.name, sha256, base64: btoa(binary), text: () => new TextDecoder().decode(bytes) };
}

export function table(target, rows, columns) {
  target.replaceChildren();
  const head = el("tr");
  for (const [, label] of columns) head.append(el("th", label));
  const thead = el("thead"); thead.append(head);
  const tbody = el("tbody");
  for (const row of rows) {
    const tr = el("tr");
    for (const [key] of columns) {
      const value = typeof key === "function" ? key(row) : row[key];
      tr.append(el("td", value === undefined || value === null ? "—" : (typeof value === "object" ? JSON.stringify(value) : value)));
    }
    tbody.append(tr);
  }
  target.append(thead, tbody);
}

export function pre(target, value) {
  target.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

export async function guarded(button, action) {
  if (button.disabled) return;
  button.disabled = true;
  button.setAttribute("aria-busy", "true");
  try { await action(); } catch (error) { toast(error.message, "bad"); } finally { button.disabled = false; button.removeAttribute("aria-busy"); }
}
