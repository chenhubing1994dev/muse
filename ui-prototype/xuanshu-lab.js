import { $, call, el, guarded, table, toast } from "./omega-common.js";

$("xs-run").addEventListener("click", () => guarded($("xs-run"), async () => {
  const data = await call("/omega/xuanshu/fuse", { body: { question: $("xs-question").value, seed: $("xs-seed").value } });
  const result = data.result || {};
  const labels = (result.epistemic_labels || []).join(" / ");
  if (labels !== "symbolic_inspiration / unverified_hypothesis") throw new Error("标签缺失，结果已拒绝显示");
  const lenses = Object.entries(result.lenses || {}).map(([name, lens]) => ({ name, ...lens }));
  table($("xs-lenses"), lenses, [["name", "透镜"], ["status", "状态"], [(row) => row.primary || row.reason || (row.keywords || []).join("、"), "内容"]]);
  const list = $("xs-prompts");
  list.replaceChildren();
  for (const prompt of result.prompts || []) {
    const item = el("li", prompt.text);
    item.append(el("span", "symbolic_inspiration", "badge"), el("span", "unverified_hypothesis", "badge"));
    list.append(item);
  }
  $("xs-policy").textContent = result.usage_policy || "";
  toast("已生成（仅作灵感）", "ok");
}));
