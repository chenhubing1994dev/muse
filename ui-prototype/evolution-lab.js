import { $, call, el, guarded, pre, table, toast } from "./omega-common.js";

async function refresh() {
  const safety = await call("/omega/safety");
  const status = safety.status || {};
  $("ev-mode").textContent = status.mode || "—";
  $("ev-mode").className = status.mode === "running" ? "ok" : (status.mode === "killed" ? "bad" : "warn");
  $("ev-detail").textContent = status.reason || "";
  table($("ev-events"), safety.events || [], [["id", "#"], ["event", "事件"], ["actor", "操作者"], ["detail_json", "详情"]]);
  const versions = await call("/omega/evolution/versions");
  $("ev-protected").textContent = "受保护面：" + (versions.protected_surfaces || []).join("、");
  table($("ev-versions"), versions.versions || [], [["version", "版本"], ["task_id", "票据"], ["status", "状态"], ["files", "文件数"], ["signature_manifest_sha256", "签名清单"]]);
}

async function control(action) {
  const body = { action };
  if (action === "kill" || action === "reset") body.confirmation = $("ev-confirm").value.trim();
  await call("/omega/safety/control", { body });
  toast("已执行：" + action, "ok");
  await refresh();
}

for (const action of ["pause", "resume", "kill", "reset"]) {
  $("ev-" + action).addEventListener("click", () => guarded($("ev-" + action), () => control(action)));
}

$("ev-rollback").addEventListener("click", () => guarded($("ev-rollback"), async () => {
  const version = Number.parseInt($("ev-version").value, 10);
  const data = await call("/omega/evolution/rollback", { body: { version, confirmation: $("ev-rollback-confirm").value.trim() } });
  pre($("ev-result"), data.result);
  await refresh();
}));

refresh().catch((error) => toast("读取失败：" + error.message, "bad"));
