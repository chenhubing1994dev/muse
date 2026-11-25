import { $, call, el, guarded, pre, readFile, table, toast } from "./omega-common.js";

$("vf-run").addEventListener("click", () => guarded($("vf-run"), async () => {
  const data = await call("/omega/film/plan", { body: { topic: $("vf-topic").value.trim() } });
  const manifest = data.manifest || {};
  const dag = $("vf-dag");
  dag.replaceChildren();
  for (const name of manifest.dag || []) {
    const node = (manifest.nodes || {})[name] || {};
    dag.append(el("span", name + " · " + (node.status || "?"), node.status || ""));
  }
  const nodes = manifest.nodes || {};
  table($("vf-hooks"), ((nodes.hook_matrix || {}).output || {}).top || [], [["hook_id", "编号"], ["line_yue", "粤语钩子"], ["visual_interrupt", "视觉打断"], ["score", "评分"]]);
  table($("vf-shots"), (nodes.shot_director || {}).output || [], [["shot_id", "镜头"], ["size", "景别"], ["focal_length_mm", "焦段"], ["camera_move", "运镜"], ["lighting", "布光"]]);
  toast(manifest.ready_for_render ? "GPU 预检通过，可渲染" : "计划已生成；GPU 预检未通过，暂不渲染", manifest.ready_for_render ? "ok" : "info");
}));

$("vf-retention").addEventListener("click", () => guarded($("vf-retention"), async () => {
  const file = await readFile($("vf-retention-file"));
  const data = await call("/omega/film/retention", { body: { csv_text: file.text(), hook_id: $("vf-hook-id").value.trim(), video_id: $("vf-video-id").value.trim() } });
  pre($("vf-retention-result"), data);
}));
