import { $, call, el, guarded, pre, readFile, table, toast } from "./omega-common.js";

$("ue-run").addEventListener("click", () => guarded($("ue-run"), async () => {
  let hypothesis;
  try { hypothesis = JSON.parse($("ue-input").value); } catch { throw new Error("JSON 格式错误"); }
  const data = await call("/omega/unit-economics/simulate", { body: { hypothesis } });
  const sim = data.simulation || {};
  const rows = Array.isArray(sim.scenarios) ? sim.scenarios : [];
  table($("ue-table"), rows, [["units", "销量"], ["net_contribution", "净贡献（" + (sim.currency || "") + "）"], [() => sim.verdict, "结论"], [() => sim.break_even_units, "保本销量"]]);
  pre($("ue-raw"), sim);
  $("ue-raw").classList.remove("hidden");
  toast("模拟完成（仅为假设模拟）", "ok");
}));

$("quote-run").addEventListener("click", () => guarded($("quote-run"), async () => {
  const file = await readFile($("quote-file"));
  const data = await call("/omega/quotes/extract", { body: { filename: file.name, content_base64: file.base64 } });
  const items = (data.extraction && data.extraction.line_items) || [];
  table($("quote-table"), items, [["description", "项目"], ["quantity", "数量"], ["unit", "单位"], ["unit_price", "单价"], ["confidence", "可信度"], ["source", "出处"]]);
  pre($("quote-raw"), data.extraction);
}));
