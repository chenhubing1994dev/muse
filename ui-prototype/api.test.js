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
