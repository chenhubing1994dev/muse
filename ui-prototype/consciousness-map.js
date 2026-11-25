import { $, call, el, guarded, pre, table, toast } from "./omega-common.js";

const NS = "http://www.w3.org/2000/svg";
const NAMES = { attention: "注意力", working_memory: "工作记忆", long_term_memory: "长期记忆", curiosity: "好奇心",
  energy_budget: "能量预算", affective_ui_state: "情感界面", metacognition: "元认知", intent: "意图", agency: "能动性", self_model: "自我模型" };

function svg(tag, attrs, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, String(v));
  if (text) node.textContent = text;
  return node;
}

function draw(dimensions) {
  const map = $("cm-map");
  map.replaceChildren();
  const keys = Object.keys(NAMES);
  const cx = 200, cy = 200, r = 140;
  for (const ring of [0.33, 0.66, 1]) map.append(svg("circle", { cx, cy, r: r * ring, class: "ring" }));
  const points = [];
  keys.forEach((key, i) => {
    const angle = -Math.PI / 2 + (2 * Math.PI * i) / keys.length;
    const value = Number((dimensions[key] || {}).value || 0);
    map.append(svg("line", { x1: cx, y1: cy, x2: cx + r * Math.cos(angle), y2: cy + r * Math.sin(angle), class: "axis" }));
    map.append(svg("text", { x: cx + (r + 26) * Math.cos(angle) - 22, y: cy + (r + 26) * Math.sin(angle) + 4, class: "label" }, NAMES[key]));
    points.push((cx + r * value * Math.cos(angle)).toFixed(1) + "," + (cy + r * value * Math.sin(angle)).toFixed(1));
  });
  map.append(svg("polygon", { points: points.join(" "), class: "shape" }));
}

async function refresh() {
  const data = await call("/omega/consciousness");
  $("cm-phase").textContent = data.phase || "—";
  $("cm-prev").textContent = data.previous_phase ? "（上一相位：" + data.previous_phase + "）" : "";
  draw(data.dimensions || {});
  const rows = Object.entries(data.dimensions || {}).map(([key, dim]) => ({ name: NAMES[key] || key, ...dim }));
  table($("cm-table"), rows, [["name", "维度"], ["value", "数值"], ["level", "等级"], [(row) => (row.drivers || []).join("；"), "驱动证据"]]);
  table($("cm-timeline"), data.timeline || [], [["id", "#"], ["phase", "相位"], [(row) => new Date(row.created_at * 1000).toLocaleString(), "时间"]]);
  pre($("cm-sources"), (data.telemetry || {}).sources || {});
}

$("cm-refresh").addEventListener("click", () => guarded($("cm-refresh"), refresh));
refresh().catch((error) => toast("读取失败：" + error.message, "bad"));
window.setInterval(() => refresh().catch(() => {}), 15000);
