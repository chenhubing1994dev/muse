// B391 P2: unit tests for the DOM-free Portal / Super Agent OS logic (run by `npm test`).
import test from "node:test";
import assert from "node:assert/strict";
import {
  OS_VIEWS, agentPresence, describeApiError, evidenceChainView, formatBytes, phraseProgress, pollDelay, rowsChanged, hasMetaphysicsLabel, healthChecks, layoutGraph, lineDiff,
  percent, requestId, resourceBars, rosterFromRegistry, successRate, ticketStatus,
} from "./muse-os-logic.js";

test("formatBytes and percent never invent values", () => {
  assert.equal(formatBytes(1536), "1.5 KB");
  assert.equal(formatBytes(-1), "未知");
  assert.equal(formatBytes(undefined), "未知");
  assert.equal(percent(1, 4), 25);
  assert.equal(percent(1, 0), null);
  assert.equal(percent(null, 10), null);
});

test("successRate is null until a Ticket finished", () => {
  assert.equal(successRate({ succeeded: 247, failed: 1 }), 99.6);
  assert.equal(successRate({ succeeded: 0, failed: 0 }), null);
  assert.equal(successRate(null), null);
});

test("resource bars keep unmeasured CPU load and network unknown", () => {
  const bars = resourceBars({ cpu: { logical_cpu_count: 12 }, memory: { total_bytes: 100, available_bytes: 25 },
    storage: { total_bytes: 10, used_bytes: 5, free_bytes: 5 } });
  assert.equal(bars[0].value, null);
  assert.equal(bars[1].value, 75);
  assert.equal(bars[2].value, 50);
  assert.equal(bars[3].value, null);
  assert.match(bars[3].text, /未测量/);
  assert.equal(resourceBars(null)[1].text, "未知");
});

test("health checks are derived only from real API results", () => {
  const result = healthChecks({
    health: { status: "ok" },
    summary: { queue: {}, knowledge: { status: "available" }, memory: { status: "available" }, products: { videos: 0 } },
    readiness: { paths: { local_motion: { ready: true }, ai_shot_generation: { ready: false } } },
    signedReviewer: false,
  });
  assert.equal(result.total, 8);
  assert.equal(result.passed, 5);
  assert.equal(result.percent, 63);
  assert.equal(healthChecks().passed, 0);
});

test("lineDiff marks deletions and additions", () => {
  assert.deepEqual(lineDiff("a\nb", "a\nc").map((line) => line.op), ["same", "del", "add"]);
  assert.deepEqual(lineDiff("", "x").map((line) => line.op), ["add"]);
  assert.deepEqual(lineDiff("x", "").map((line) => line.op), ["del"]);
  assert.deepEqual(lineDiff("", ""), []);
});

test("ticket status labels", () => {
  assert.equal(ticketStatus("waiting_approval").label, "待审批");
  assert.equal(ticketStatus("unknown_state").tone, "muted");
});

test("agent presence never fabricates an online Agent", () => {
  const now = Date.parse("2026-11-27T02:00:00Z");
  assert.equal(agentPresence({ enabled: true, last_seen_at: "2026-11-27T01:59:30Z", stale_after_seconds: 120 }, now).state, "online");
  assert.equal(agentPresence({ enabled: true, last_seen_at: "2026-11-27T01:00:00Z", stale_after_seconds: 120 }, now).state, "stale");
  assert.equal(agentPresence({ enabled: true }, now).state, "never_seen");
  assert.equal(agentPresence({ enabled: false }, now).state, "disabled");
  assert.equal(agentPresence({ enabled: true, last_seen_at: "2026-11-27T01:59:30Z", stale_after_seconds: 120, current_task_id: "t" }, now).state, "busy");
});

test("roster maps registry Agents onto Commander + six expert roles", () => {
  const roster = rosterFromRegistry([{ agent_id: "muse-researcher-1", role: "Researcher" }]);
  assert.equal(roster.length, 7);
  assert.equal(roster[0].id, "commander");
  assert.equal(roster[0].members.length, 0);
  assert.equal(roster[1].members.length, 1);
});

test("requestId produces 32 lowercase hex characters", () => {
  assert.equal(requestId(new Uint8Array(16).fill(171)), "ab".repeat(16));
  assert.match(requestId(), /^[0-9a-f]{32}$/);
});

test("graph layout stays inside the canvas and ignores dangling edges", () => {
  const nodes = Array.from({ length: 30 }, (_, i) => ({ id: "n" + i, node_type: "claim" }));
  const out = layoutGraph(nodes, [{ source_id: "n0", target_id: "n1" }, { source_id: "n1", target_id: "missing" }], 400, 300, 40);
  assert.equal(out.length, 30);
  assert.ok(out.every((node) => node.x >= 16 && node.x <= 384 && node.y >= 16 && node.y <= 284));
});

test("metaphysics label detection", () => {
  assert.ok(hasMetaphysicsLabel({ epistemic_display_label: "玄学推演／待验证假设", is_evidence: false }));
  assert.ok(!hasMetaphysicsLabel({ epistemic_display_label: "玄学推演／待验证假设", is_evidence: true }));
  assert.ok(!hasMetaphysicsLabel({}));
});

test("Super Agent OS exposes exactly the eight modules", () => {
  assert.deepEqual(OS_VIEWS.map((view) => view.label), ["总览", "任务中心", "智能体", "记忆库", "知识图谱", "工具箱", "工作流", "设置"]);
});

test("phraseProgress validates exact confirmation phrases", () => {
  assert.equal(phraseProgress("", "APPROVE-TICKET").state, "empty");
  assert.equal(phraseProgress("APPROVE", "APPROVE-TICKET").state, "partial");
  assert.equal(phraseProgress("APPROVE-TICKET", "APPROVE-TICKET").ok, true);
  assert.equal(phraseProgress("approve-ticket", "APPROVE-TICKET").state, "mismatch");
  assert.equal(phraseProgress("APPROVE-TICKET ", "APPROVE-TICKET").ok, false);
  assert.equal(phraseProgress("x", "").ok, false);
});

test("pollDelay slows down hidden pages and has a floor", () => {
  assert.equal(pollDelay(15000, false), 15000);
  assert.equal(pollDelay(15000, true), 60000);
  assert.equal(pollDelay(1, false), 5000);
  assert.equal(pollDelay(NaN, true), 60000);
});

test("describeApiError keeps the backend status code visible", () => {
  assert.equal(describeApiError(409, { error: "explicit APPROVE-TICKET confirmation is required" }),
    "HTTP 409 · explicit APPROVE-TICKET confirmation is required（被安全门禁拒绝或状态冲突）");
  assert.equal(describeApiError(0, null, "Failed to fetch"), "网络错误 · Failed to fetch");
  assert.equal(describeApiError(503, {}), "HTTP 503 · 依赖服务暂不可用");
});

test("rowsChanged detects real changes only", () => {
  const a = [{ id: "1", s: "queued" }];
  assert.equal(rowsChanged(a, [{ id: "1", s: "queued" }]), false);
  assert.equal(rowsChanged(a, [{ id: "1", s: "running" }]), true);
  assert.equal(rowsChanged(a, []), true);
  assert.equal(rowsChanged(null, a), true);
});

test("evidenceChainView separates symbolic origin from empirical feedback", () => {
  const view = evidenceChainView({
    claim_status: "proposed",
    origin: { kind: "symbolic_hypothesis", epistemic_display_label: "玄学推演／待验证假设", is_evidence: false },
    feedback_records: [{ id: "f1", recorded_at: "2026-11-27T04:00:00+00:00", creator_verdict: "supports",
      video_sha256: "a".repeat(64), independent_evidence: false, feedback: "完播率上升" },
    { id: "f2", creator_verdict: "x", video_sha256: "not-a-hash" }],
  });
  assert.equal(view.originLabel, "玄学推演／待验证假设");
  assert.equal(view.originIsEvidence, false);
  assert.equal(view.claimStatus, "proposed");
  assert.equal(view.records[0].verdict, "支持假设");
  assert.equal(view.records[0].sha, "a".repeat(64));
  assert.equal(view.records[1].sha, "未知");
  assert.equal(view.records[1].verdict, "未记录");
  assert.equal(view.records[1].when, "未知时间");
  assert.equal(evidenceChainView(null).records.length, 0);
});
