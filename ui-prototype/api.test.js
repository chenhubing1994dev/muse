import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient, resolveDesktopConnection } from "./api.js";
const token = "test-token-that-is-long-enough-for-the-api";

test("API client sends bearer token and disables cache", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, agents: [], count: 0 }) };
    },
  });
  const payload = await client.agents();
  assert.equal(payload.count, 0);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/agents?limit=200");
  assert.equal(calls[0].options.headers.Authorization, `Bearer ${token}`);
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.redirect, "error");
});

test("API client refuses non-loopback hosts and unsafe base URLs", () => {
  assert.throws(
    () => createApiClient({ baseUrl: "http://192.168.1.10:8765", token }),
    /同源控制台/,
  );
  assert.throws(
    () => createApiClient({ baseUrl: "http://user@localhost:8765", token }),
    /同源控制台/,
  );
  assert.throws(
    () => createApiClient({ baseUrl: "http://localhost:8765/untrusted", token }),
    /同源控制台/,
  );  assert.throws(
    () => createApiClient({ baseUrl: "http://localhost:8765", token, timeoutMs: 50 }),
    RangeError,
  );
});

test("API client accepts the authenticated same-origin LAN dashboard proxy", () => {
  const previous = globalThis.window;
  globalThis.window = { location: { origin: "http://192.168.68.173:8766" } };
  try {
    assert.doesNotThrow(() => createApiClient({
      baseUrl: "http://192.168.68.173:8766/",
      token,
      fetchImpl: async () => ({ ok: true, json: async () => ({ api_version: 1 }) }),
    }));
  } finally {
    if (previous === undefined) delete globalThis.window;
    else globalThis.window = previous;
  }
});

test("API client reports HTTP errors and incompatible API versions", async () => {
  const denied = createApiClient({
    baseUrl: "http://localhost:8765", token,
    fetchImpl: async () => ({ ok: false, status: 401 }),
  });
  await assert.rejects(denied.health(), /HTTP 401/);

  const incompatible = createApiClient({
    baseUrl: "http://localhost:8765", token,
    fetchImpl: async () => ({ ok: true, json: async () => ({ api_version: 2 }) }),
  });
  await assert.rejects(incompatible.health(), /版本不兼容/);
});

test("events limit is validated before any request", () => {
  const client = createApiClient({
    baseUrl: "http://localhost:8765", token,
    fetchImpl: async () => { throw new Error("must not be called"); },
  });
  assert.throws(() => client.events(101), RangeError);
});

test("initiative timeline validates its id and bounds before authenticated GET", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({
        api_version: 1, initiative: { id: "a".repeat(32) }, events: [], history_complete: true,
      }) };
    },
  });
  const result = await client.initiativeTimeline("a".repeat(32), 20);
  assert.equal(result.history_complete, true);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/initiatives/" + "a".repeat(32) + "/timeline?limit=20");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
  assert.throws(() => client.initiativeTimeline("../bad"), /编号无效/);
  assert.throws(() => client.initiativeTimeline("a".repeat(32), 201), RangeError);
});

test("initiative performance client authenticates GET and append-only POST with validated identifiers", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, snapshots: [], summary_by_currency: [], snapshot: { id: "b".repeat(32) } }) };
    },
  });
  const id = "a".repeat(32);
  await client.initiativePerformance(id);
  await client.recordInitiativePerformance(id, {
    idempotency_key: "manual-snapshot-0001", platform: "youtube", source: "manual_entry",
    currency: "USD", metrics: { views: 12, settled_revenue_minor: 250 },
  });
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/initiatives/" + id + "/performance");
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.headers.Authorization, "Bearer " + token);
  assert.equal(JSON.parse(calls[1].options.body).metrics.settled_revenue_minor, 250);
  assert.equal(calls[1].options.cache, "no-store");
  assert.throws(() => client.initiativePerformance("../bad"), /编号无效/);
  assert.throws(() => client.recordInitiativePerformance(id, { idempotency_key: "x", platform: "youtube", source: "manual_entry", metrics: {} }), /幂等键无效/);
});

test("SSE client sends bearer and cursor, parses redacted events, and returns the new cursor", async () => {
  const calls = [];
  const seen = [];
  const id = "a".repeat(32);
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return {
        ok: true,
        headers: { get: () => "text/event-stream; charset=utf-8" },
        text: async () => "id: " + id + "\nevent: muse-progress\ndata: {\"category\":\"task\",\"event\":\"completed\",\"created_at\":\"2026-11-06T00:00:00Z\"}\n\n: keep-alive\n\n",
      };
    },
  });
  const cursor = await client.streamEvents({ lastEventId: "b".repeat(32), signal: new AbortController().signal, onEvent: (event) => seen.push(event) });
  assert.equal(cursor, id);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].event, "completed");
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/events/stream");
  assert.equal(calls[0].options.headers.Authorization, `Bearer ${token}`);
  assert.equal(calls[0].options.headers["Last-Event-ID"], "b".repeat(32));
  assert.equal(calls[0].options.cache, "no-store");
});

test("knowledge node detail validates the node id and uses the authenticated API", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, node: { id: "d".repeat(32), node_type: "decision" } }) };
    },
  });
  await assert.rejects(async () => client.knowledgeNode("../secret"), /知识节点编号无效/);
  const result = await client.knowledgeNode("d".repeat(32));
  assert.equal(result.node.node_type, "decision");
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/knowledge/nodes/" + "d".repeat(32));
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
});
test("claim dossier validates id and evidence limit and uses authenticated read-only API", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, dossier: { claim_id: "e".repeat(32), evidence: [] } }) };
    },
  });
  await assert.rejects(async () => client.knowledgeClaimDossier("../secret"), /Claim 编号无效/);
  await assert.rejects(async () => client.knowledgeClaimDossier("e".repeat(32), 0), /证据条数/);
  const result = await client.knowledgeClaimDossier("e".repeat(32), 24);
  assert.equal(result.dossier.claim_id, "e".repeat(32));
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/knowledge/claims/" + "e".repeat(32) + "/dossier?evidence_limit=24");
  assert.equal(calls[0].options.method, "GET");  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
});

test("SSE client clears an expired cursor when the server requests a snapshot reset", async () => {
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async () => ({
      ok: true,
      headers: { get: () => "text/event-stream" },
      text: async () => "event: muse-reset\ndata: {}\n\n",
    }),
  });
  const cursor = await client.streamEvents({
    lastEventId: "c".repeat(32),
    signal: new AbortController().signal,
    onEvent: () => assert.fail("reset must not be surfaced as a progress event"),
  });
  assert.equal(cursor, "");
});

test("feedback submission sends only structured allowlisted fields through authenticated POST", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, feedback: {
        category: "usability", tag: "workflow_gap", severity: 4, created_at: "2026-11-03T00:00:00Z",
      } }) };
    },
  });
  const body = { category: "usability", tag: "workflow_gap", severity: 4 };
  const result = await client.submitFeedback(body);
  assert.equal(result.feedback.severity, 4);
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
  assert.deepEqual(JSON.parse(calls[0].options.body), body);
  assert.equal(calls[0].options.redirect, "error");
  assert.equal(calls[0].options.cache, "no-store");
});

test("creator initiative review feedback uses a validated authenticated endpoint", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, feedback: {
        id: "f".repeat(32), decision: "revise", evolution_cycle_id: "cycle_" + "a".repeat(32),
      } }) };
    },
  });
  const initiativeId = "b".repeat(32);
  const cycleId = "cycle_" + "a".repeat(32);
  assert.throws(
    () => client.submitInitiativeFeedback("../bad", { feedback: "Review", decision: "revise" }),
    /创作项目编号无效/,
  );
  assert.throws(
    () => client.submitInitiativeFeedback(initiativeId, { feedback: "Review", decision: "approve" }),
    /评审决定无效/,
  );
  assert.throws(
    () => client.submitInitiativeFeedback(initiativeId, { feedback: "  ", decision: "revise" }),
    /反馈内容/,
  );
  const result = await client.submitInitiativeFeedback(initiativeId, {
    feedback: "请修复镜头衔接", decision: "revise", evolution_cycle_id: cycleId,
  });
  assert.equal(result.feedback.decision, "revise");
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/initiatives/" + initiativeId + "/feedback");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    feedback: "请修复镜头衔接", decision: "revise", reviewer: "creator",
    source: "dashboard", evolution_cycle_id: cycleId,
  });
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.redirect, "error");
});


test("video attempt metrics use the authenticated read-only dashboard endpoint", async () => {
  const calls = [];
  const metrics = {
    total: 4, succeeded: 2, failed: 1, pending: 1, terminal: 3,
    success_rate: 2 / 3, success_rate_numerator: 2, success_rate_denominator: 3,
    scope: "persisted_video_attempts",
  };
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, metrics, attempts: [] }) };
    },
  });
  const result = await client.videoAttempts();
  assert.equal(result.metrics.success_rate_denominator, 3);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/video-attempts");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.redirect, "error");
});

test("video recipe trace uses the authenticated read-only endpoint and validates attempt ids", async () => {
  const calls = [];
  const trace = { read_only: true, chain_consistent: true, publishing: "not_performed" };
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, trace }) };
    },
  });
  const result = await client.videoRecipeTrace("attempt_01");
  assert.equal(result.trace.chain_consistent, true);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/video-recipe-traces/attempt_01");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[0].options.redirect, "error");
  assert.throws(() => client.videoRecipeTrace("../bad"), /视频任务编号无效/);
});

test("feedback listing validates bounds and improvement proposals use the authenticated API", async () => {  const calls = [];
  const client = createApiClient({
    baseUrl: "http://localhost:8765", token,
    fetchImpl: async (url) => {
      calls.push(String(url));
      return { ok: true, json: async () => ({ api_version: 1, count: 0, feedback: [], proposals: [] }) };
    },
  });
  await assert.rejects(async () => client.feedback(101), RangeError);
  await client.feedback(20);
  await client.improvements();
  await client.selfImprovement();
  await client.runRadarScan();
  assert.deepEqual(calls, [
    "http://localhost:8765/api/v1/feedback?limit=20",
    "http://localhost:8765/api/v1/improvements",
    "http://localhost:8765/api/v1/self-improvement",
    "http://localhost:8765/api/v1/radar/scan",
  ]);
});

test("desktop connection is obtained through the Tauri v2 IPC bridge", async () => {
test("proposal detail uses a validated id and authenticated no-store GET", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://localhost:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, proposal: { id: "a".repeat(32), title: "Review" } }) };
    },
  });
  await client.improvementDetail("a".repeat(32));
  assert.equal(calls[0].url, "http://localhost:8765/api/v1/improvements/" + "a".repeat(32));
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
  assert.throws(() => client.improvementDetail("../bad"), /编号无效/);
});

  const previousWindow = globalThis.window;
  let invokedCommand = "";
  globalThis.window = {
    __TAURI__: {
      core: {
        invoke: async (command) => {
          invokedCommand = command;
          return { baseUrl: "http://127.0.0.1:43210", token };
        },
      },
    },
  };
  try {
    const connection = await resolveDesktopConnection();
    assert.equal(invokedCommand, "get_status_connection");
    assert.equal(connection.baseUrl, "http://127.0.0.1:43210");
    assert.equal(connection.token, token);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("local browser dashboard connection uses the ephemeral same-origin bootstrap", async () => {
  const previousWindow = globalThis.window;
  const expected = { baseUrl: "http://127.0.0.1:43211", token };
  globalThis.window = { __MUSE_LOCAL_CONNECTION__: expected };
  try {
    assert.deepEqual(await resolveDesktopConnection(), expected);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("inspiration, report and product endpoints use authenticated local API requests", async () => {
  const calls = [];
  const blob = new Blob(["artifact"]);
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      if (String(url).includes("/artifacts/") || String(url).includes("/products/")) {
        return { ok: true, blob: async () => blob };
      }
      return { ok: true, json: async () => ({ api_version: 1, inspirations: [], artifacts: [], products: [], count: 0 }) };
    },
  });
  await client.inspirations(25);
  await client.artifacts(10);
  await client.products(15);
  await client.submitInspiration({
    content: "A creative idea", allow_external_research: true,
    title: "Cross-domain idea", tags: ["creation"],
    research_domains: ["creative-ai"], source: "chatgpt_daily_inspiration",
  });
  const downloaded = await client.downloadArtifact("autonomous-research-example.md");
  const productZip = await client.downloadProduct("0123456789abcdef0123456789abcdef", "0123456789abcdef0123");
  const productPreview = await client.previewProduct("0123456789abcdef0123456789abcdef", "0123456789abcdef0123");
  assert.equal(downloaded, blob);
  assert.equal(productZip, blob);
  assert.equal(productPreview, blob);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/inspirations?limit=25");
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/artifacts?limit=10");
  assert.equal(calls[2].url, "http://127.0.0.1:8765/api/v1/products?limit=15");
  assert.equal(calls[3].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[3].options.body), {
    content: "A creative idea", allow_external_research: true,
    title: "Cross-domain idea", tags: ["creation"],
    research_domains: ["creative-ai"], source: "chatgpt_daily_inspiration",
  });
  assert.equal(calls[4].options.headers.Authorization, `Bearer ${token}`);
  assert.equal(calls[5].url, "http://127.0.0.1:8765/api/v1/products/0123456789abcdef0123456789abcdef/0123456789abcdef0123/download");
  assert.equal(calls[6].url, "http://127.0.0.1:8765/api/v1/products/0123456789abcdef0123456789abcdef/0123456789abcdef0123/preview");
  assert.equal(calls[5].options.cache, "no-store");
  assert.equal(calls[6].options.redirect, "error");
  await assert.rejects(client.downloadProduct("../escape", "0123456789abcdef0123"), /产品编号无效/);
});

test("video product jobs and MP4 downloads use authenticated local endpoints", async () => {
  const calls = [];
  const blob = new Blob(["mp4"]);
  const jobId = "b".repeat(32);
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      if (String(url).endsWith("/video")) return { ok: true, blob: async () => blob };
      if (String(url).endsWith("/video-products")) return { ok: true, json: async () => ({ api_version: 1, job: { id: jobId, status: "queued" } }) };
      return { ok: true, json: async () => ({ api_version: 1, job: { id: jobId, status: "running" } }) };
    },
  });
  const created = await client.createVideoProduct({ source: "demo", title: "Local test", audio_mode: "narration" });
  const status = await client.videoJob(created.job.id);
  const downloaded = await client.downloadVideo("0123456789abcdef0123456789abcdef", "0123456789abcdef0123");
  assert.equal(status.job.status, "running");
  assert.equal(downloaded, blob);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/video-products");
  assert.equal(calls[0].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[0].options.body), { source: "demo", title: "Local test", audio_mode: "narration" });
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/video-jobs/" + jobId);
  assert.equal(calls[2].url, "http://127.0.0.1:8765/api/v1/products/0123456789abcdef0123456789abcdef/0123456789abcdef0123/video");
  assert.equal(calls[2].options.cache, "no-store");
  assert.throws(() => client.videoJob("../bad"), /任务编号无效/);
});

test("subtitle review workbench loads cue data and requires authenticated explicit approval POST", async () => {
  const calls = [];
  const identity = "0123456789abcdef0123456789abcdef";
  const version = "0123456789abcdef0123";
  const review = {
    schema_version: 1, review_status: "pending_human_review", human_approved: false,
    segments: [{ index: 1, start_seconds: 0, end_seconds: 1, asr_text: "原文", review_status: "pending", corrected_text: null }],
  };
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => String(url).endsWith("/approve")
        ? { api_version: 1, product: { initiative_id: identity, version_id: version, validated: true, status: "testing" } }
        : { api_version: 1, review } };
    },
  });
  const loaded = await client.subtitleReview(identity, version);
  assert.deepEqual(loaded.review, review);
  const payload = {
    reviewed_segment_indexes: [1],
    corrections: [{ segment_index: 1, corrected_text: "修订文案" }],
    reviewer: "creator",
    reviewer_id: "local-editor-1",
    reviewer_role: "subtitle_editor",
    rationale: "Checked every subtitle cue against narration.",
    evidence_refs: ["subtitle-review.json#segments", "narration.wav#sha256"],
    confirmation: "APPROVE-HUMAN-REVIEW",
  };  const result = await client.approveSubtitleReview(identity, version, payload);
  assert.equal(result.product.status, "testing");
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/products/" + identity + "/" + version + "/subtitle-review");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/products/" + identity + "/" + version + "/subtitle-review/approve");
  assert.equal(calls[1].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[1].options.body), payload);
  assert.throws(() => client.subtitleReview("../bad", version), /产品编号无效/);
});

test("MemPalace status uses an authenticated read-only local endpoint", async () => {
  const calls = [];
  const expected = {
    api_version: 1, config_state: "configured", enabled: true,
    connection_state: "local_store_present", palace_directory_present: true,
    vector_index_present: true, index_freshness: "not_measured",
    embedding_model: "embeddinggemma", embedding_device: "cpu",
    memory_store_state: "ready",
    projection_outbox: { initialized: true, status: "attention_required", total: 7, pending: 2, failed: 1, delivered: 4, oldest_pending_at: "2026-11-19T10:00:00Z", latest_delivered_at: "2026-11-19T12:00:00Z", latest_created_at: "2026-11-19T12:00:00Z", content_included: false },
    remote_process_started: false, private_content_exposed: false,
  };
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => expected };
    },
  });
  assert.deepEqual(await client.mempalaceStatus(), expected);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/mempalace/status");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
});

test("overview preserves the safe queue and worker status payload", async () => {

  const expected = {
    api_version: 1,
    queue: {
      counts: { queued: 2, running: 1, waiting_approval: 1, succeeded: 4, failed: 0, cancelled: 0 },
      active: [{ id: "task-1", title: "Example", status: "queued", attempts: 0 }],
      active_truncated: false,
    },
    workers: [{ worker_id: "worker-1", state: "active", heartbeat_at: "2026-11-03T00:00:00Z", stale: false }],
  };
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async () => ({ ok: true, json: async () => expected }),
  });
  assert.deepEqual(await client.overview(), expected);
});

test("daemon status and lifecycle control use authenticated GET/POST", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, result: { changed: true }, state: "running" }) };
    },
  });
  await client.daemonStatus();
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/daemon/status");
  assert.equal(calls[0].options.method, "GET");
  await client.daemonControl("pause");
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/daemon/pause");
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.headers.Authorization, "Bearer " + token);
  assert.throws(() => client.daemonControl("rm-rf"), /控制动作无效/);
});

test("pipeline submit/recipe/job endpoints validate input and use POST", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, job: { job_id: "a".repeat(32) } }) };
    },
  });
  await client.pipelineRecipes();
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/pipelines/recipes");
  await client.pipelineJobs(5);
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/pipelines/jobs?limit=5");
  await client.submitPipeline({ recipe_id: "high_value_short_v2" });
  assert.equal(calls[2].url, "http://127.0.0.1:8765/api/v1/pipelines/submit");
  assert.equal(calls[2].options.method, "POST");
  assert.equal(JSON.parse(calls[2].options.body).recipe_id, "high_value_short_v2");
  assert.throws(() => client.pipelineJob("../bad"), /编号无效/);
  assert.throws(() => client.submitPipeline({}), /配方编号/);
  assert.throws(() => client.pipelineJobs(0), RangeError);
});

test("cyber cosmos endpoints validate inputs and send authenticated requests", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, ok: true }) };
    },
  });
  await client.prometheusCore();
  await client.knowledgeGraph({ limit: 80, nodeType: "claim", q: "muse" });
  await client.galaxy();
  await client.previewRoute("研究系统架构");
  await client.celestialCatalog();
  await client.castYijing({ mode: "seeded_cast", seed: "ember" });
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/prometheus/core");
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/knowledge/graph?limit=80&node_type=claim&q=muse");
  assert.equal(calls[2].url, "http://127.0.0.1:8765/api/v1/galaxy");
  assert.equal(calls[3].url, "http://127.0.0.1:8765/api/v1/galaxy/route-preview");
  assert.equal(calls[3].options.method, "POST");
  assert.equal(calls[4].url, "http://127.0.0.1:8765/api/v1/metaphysics/celestial");
  assert.equal(calls[5].url, "http://127.0.0.1:8765/api/v1/metaphysics/yijing");
  assert.equal(calls[5].options.method, "POST");
  assert.throws(() => client.knowledgeGraph({ limit: 999 }), RangeError);
  assert.throws(() => client.knowledgeGraph({ nodeType: "invalid" }), /类型无效/);
  assert.throws(() => client.previewRoute("   "), /目标/);
  assert.throws(() => client.castYijing({ mode: "bad" }), /模式无效/);
});

test("evolveInspiration validates id and sends POST /api/v1/inspirations/evolve", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765",
    token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, evolution: { status: "completed" } }) };
    },
  });
  await client.evolveInspiration({ inspiration_id: "a".repeat(32), enqueue_video: true, aspect_ratios: ["9:16", "16:9"] });
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/inspirations/evolve");
  assert.equal(calls[0].options.method, "POST");
  assert.equal(JSON.parse(calls[0].options.body).enqueue_video, true);
  assert.throws(() => client.evolveInspiration({ inspiration_id: "bad-id" }), /编号无效/);
});
