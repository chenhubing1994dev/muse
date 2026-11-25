import { $, call, guarded, pre, readFile, toast } from "./omega-common.js";

function parseList(id) {
  try {
    const value = JSON.parse($(id).value || "[]");
    if (!Array.isArray(value)) throw new Error();
    return value;
  } catch { throw new Error(id + " 必须是 JSON 数组"); }
}

$("rb-run").addEventListener("click", () => guarded($("rb-run"), async () => {
  const data = await call("/omega/research/investigate", { body: {
    claim_id: $("rb-claim-id").value.trim(), claim: $("rb-claim").value.trim(),
    support_sources: parseList("rb-support"), counter_sources: parseList("rb-counter"), citations: parseList("rb-citations") } });
  pre($("rb-dossier"), data.dossier);
  toast("档案袋已追加：" + (data.dossier || {}).verdict, "ok");
}));

$("rb-upload").addEventListener("click", () => guarded($("rb-upload"), async () => {
  const file = await readFile($("rb-file"));
  const data = await call("/omega/feedback/upload", { body: { filename: file.name, content_base64: file.base64, declared_sha256: file.sha256 } });
  pre($("rb-upload-result"), data.result);
  toast(data.result.status === "duplicate" ? "已存在（幂等，不重复追加）" : "上传成功，已连回 Claim", "ok");
}));

$("rb-lookup").addEventListener("click", () => guarded($("rb-lookup"), async () => {
  const id = $("rb-lookup-id").value.trim();
  if (!/^[a-z0-9][a-z0-9._-]{0,95}$/.test(id)) throw new Error("Claim ID 格式不对");
  pre($("rb-lookup-result"), await call("/omega/claims/" + id));
}));
