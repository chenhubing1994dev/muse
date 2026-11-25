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

export async function call(path, options = {}) {
  const init = { credentials: "same-origin", headers: { Accept: "application/json" }, method: options.method || "GET" };
  if (options.body !== undefined) {
    init.method = options.method || "POST";
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const response = await fetch(API + path, init);
  let data = {};
  try { data = await response.json(); } catch { data = { error: "服务器返回了非 JSON 内容" }; }
  if (!response.ok) throw new Error(data.error || ("请求失败：" + response.status));
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
  try { await action(); } catch (error) { toast(error.message, "bad"); } finally { button.disabled = false; }
}
