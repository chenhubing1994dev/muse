import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient, createCompanionRemoteClient, resolveDesktopConnection } from "./api.js";
import { createHash, createHmac, webcrypto } from "node:crypto";
const token = "test-token-that-is-long-enough-for-the-api";

test("Companion host management client uses only authenticated local management routes", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, pairings: [], devices: [], events: [], challenge: { challenge_id: "a".repeat(32), code: "one-time-code", expires_at: 123 }, pairing: { request_id: "b".repeat(32), status: "approved" }, denied: true, revoked: true }) };
    },
  });
  await client.companionPairings(12);
  await client.companionDevices(20);
  await client.companionAudit(30);
  await client.startCompanionPairing(60);
  await client.approveCompanionPairing("b".repeat(32));
  await client.denyCompanionPairing("b".repeat(32));
  await client.revokeCompanionDevice("c".repeat(32));
  assert.deepEqual(calls.map((call) => call.url), [
    "http://127.0.0.1:8765/api/v1/companion/pairings?limit=12",
    "http://127.0.0.1:8765/api/v1/companion/devices?limit=20",
    "http://127.0.0.1:8765/api/v1/companion/audit?limit=30",
    "http://127.0.0.1:8765/api/v1/companion/pairing/start",
    "http://127.0.0.1:8765/api/v1/companion/pairings/" + "b".repeat(32) + "/approve",
    "http://127.0.0.1:8765/api/v1/companion/pairings/" + "b".repeat(32) + "/deny",
    "http://127.0.0.1:8765/api/v1/companion/devices/" + "c".repeat(32) + "/revoke",
  ]);
  assert.equal(calls[3].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[3].options.body), { expires_in_seconds: 60 });
  for (const call of calls) assert.equal(call.options.headers.Authorization, "Bearer " + token);
  assert.throws(() => client.startCompanionPairing(30), RangeError);
  assert.throws(() => client.approveCompanionPairing("../bad"), /编号无效/);
  assert.throws(() => client.revokeCompanionDevice("../bad"), /编号无效/);
  assert.throws(() => client.companionDevices(201), RangeError);
});

test("Companion remote client performs HTTPS-only pairing and HMAC-signed read requests without desktop Bearer", async () => {
  const calls = [];
  const deviceId = "a".repeat(32);
  const deviceToken = "device-token-secret-value-that-is-long-enough";
  const client = createCompanionRemoteClient({
    baseUrl: "https://muse.example.com:8766",
    cryptoImpl: webcrypto,
    fetchImpl: async (url, options) => {
      const parsed = new URL(String(url));
      calls.push({ url: String(url), options });
      if (parsed.pathname.endsWith("/pairing/request")) {
        return { ok: true, status: 202, json: async () => ({
          api_version: 1,
          pairing: { request_id: "b".repeat(32), status: "pending", expires_at: 2000000000 },
        }) };
      }
      if (parsed.pathname.endsWith("/pairing/claim")) {
        return { ok: true, status: 200, json: async () => ({
          api_version: 1,
          device: { device_id: deviceId, token: deviceToken, permissions: ["events.read", "status.read"] },
        }) };
      }
      if (parsed.pathname.endsWith("/status")) {
        return { ok: true, status: 200, json: async () => ({
          api_version: 1, host: { status: "online", read_only: true },
        }) };
      }
      return { ok: true, status: 200, json: async () => ({
        api_version: 1, reset: false, events: [],
      }) };
    },
  });

  const pairing = await client.requestPairing({
    challengeId: "c".repeat(32), code: "one-time-host-challenge-code",
    deviceName: "Muse iPhone",
  });
  assert.equal(pairing.requestId, "b".repeat(32));
  assert.equal(client.hasPendingPairing, true);
  const pairingBody = JSON.parse(calls[0].options.body);
  assert.equal(pairingBody.challenge_id, "c".repeat(32));
  assert.match(pairingBody.claim_secret, /^[A-Za-z0-9_-]{43}$/);
  assert.match(pairingBody.device_fingerprint, /^[a-f0-9]{64}$/);

  await client.claim();
  assert.equal(client.isPaired, true);
  assert.equal(client.hasPendingPairing, false);
  assert.deepEqual(client.permissions, ["events.read", "status.read"]);
  const status = await client.getStatus();
  assert.equal(status.host.status, "online");
  await client.getEvents("d".repeat(32));

  assert.equal(calls.length, 4);
  for (const call of calls) {
    const options = call.options;
    assert.equal(options.credentials, "omit");
    assert.equal(options.mode, "cors");
    assert.equal(options.cache, "no-store");
    assert.equal(options.redirect, "error");
    assert.equal(options.referrerPolicy, "no-referrer");
    assert.equal(options.headers.Authorization, undefined);
  }

  const emptyBodyHash = createHash("sha256").update(Buffer.alloc(0)).digest("hex");
  for (const call of calls.slice(2)) {
    const headers = call.options.headers;
    const path = new URL(call.url).pathname;
    const cursor = headers["Last-Event-ID"] || "";
    const canonical = [
      "muse-companion-v1", deviceId, headers["X-Muse-Session-ID"],
      headers["X-Muse-Nonce"], headers["X-Muse-Timestamp"],
      "GET", path, cursor, emptyBodyHash,
    ].join("\n");
    const expected = createHmac("sha256", deviceToken).update(canonical).digest("hex");
    assert.equal(headers["X-Muse-Signature"], expected);
    assert.match(headers["X-Muse-Session-ID"], /^[a-f0-9]{32}$/);
    assert.match(headers["X-Muse-Nonce"], /^[A-Za-z0-9_-]{32}$/);
  }
  assert.equal(calls[3].options.headers["Last-Event-ID"], "d".repeat(32));

  client.forget();
  assert.equal(client.isPaired, false);
  await assert.rejects(client.getStatus(), /尚未完成配对/);
});

test("Companion remote client rejects HTTP, userinfo, and non-root Host URLs", () => {
  const options = { fetchImpl: async () => { throw new Error("must not fetch"); }, cryptoImpl: webcrypto };
  assert.throws(() => createCompanionRemoteClient({ ...options, baseUrl: "http://muse.example.com" }), /HTTPS/);
  assert.throws(() => createCompanionRemoteClient({ ...options, baseUrl: "https://user@muse.example.com" }), /HTTPS/);
  assert.throws(() => createCompanionRemoteClient({ ...options, baseUrl: "https://muse.example.com/untrusted" }), /HTTPS/);
  assert.throws(() => createCompanionRemoteClient({ ...options, baseUrl: "https://127.0.0.1:8766" }), /局域网/);
  assert.throws(() => createCompanionRemoteClient({ ...options, baseUrl: "https://192.168.1.8:8766" }), /局域网/);
  assert.throws(() => createCompanionRemoteClient({ ...options, baseUrl: "https://muse.local" }), /局域网/);
  assert.throws(() => createCompanionRemoteClient({ ...options, baseUrl: "https://muse.local." }), /局域网/);
  assert.throws(() => createCompanionRemoteClient({ ...options, baseUrl: "https://localhost." }), /局域网/);
});

test("Companion host filtering parses IPv6 ranges without rejecting public DNS names", () => {
  const options = { fetchImpl: async () => { throw new Error("must not fetch"); }, cryptoImpl: webcrypto };
  for (const baseUrl of ["https://fc.example.com", "https://fe80.example.com", "https://fda.example.com"]) {
    assert.doesNotThrow(() => createCompanionRemoteClient({ ...options, baseUrl }), baseUrl);
  }
  for (const baseUrl of [
    "https://[::]", "https://[::1]", "https://[fc00::1]", "https://[fd12:3456::1]",
    "https://[fe80::1]", "https://[ff02::1]", "https://[::ffff:192.168.1.8]",
  ]) {
    assert.throws(() => createCompanionRemoteClient({ ...options, baseUrl }), /局域网/, baseUrl);
  }
  assert.doesNotThrow(() => createCompanionRemoteClient({
    ...options, baseUrl: "https://[2001:4860:4860::8888]",
  }));
});

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
  );
  assert.throws(
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

test("video contact sheet uses authenticated, cache-free product asset route", async () => {
  const calls = [];
  const image = new Blob(["verified-png"]);
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, blob: async () => image };
    },
  });
  const blob = await client.videoContactSheet("a".repeat(32), "b".repeat(20));
  assert.equal(blob, image);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/products/" + "a".repeat(32) + "/" + "b".repeat(20) + "/contact-sheet");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
});

test("visual review client reads and appends authenticated hash-bound review records", async () => {
  const calls = [];
  const id = "a".repeat(32);
  const version = "b".repeat(20);
  const record = { record_id: "c".repeat(32), decision: "approved" };
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return {
        ok: true,
        json: async () => ({ api_version: 1, identity_assurance: "self_asserted_local_session", count: 1, reviews: [record], record, append_only: true, digital_signature: false }),
      };
    },
  });
  const history = await client.visualReviews(id, version);
  const payload = {
    decision: "approved", reviewer: "Local Editor", reviewer_role: "video_editor",
    rationale: "Reviewed the complete video and sampled keyframes carefully.",
    checklist: {
      subject_framing: true, shot_continuity: true, visual_anomalies: true,
      subtitle_visibility: true, full_video_playback: true,
    },
    confirmation: "RECORD-VISUAL-REVIEW",
  };
  const saved = await client.submitVisualReview(id, version, payload);
  assert.equal(history.reviews[0], record);
  assert.equal(saved.record, record);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/products/" + id + "/" + version + "/visual-review");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[1].options.method, "POST");
  assert.equal(calls[1].options.headers.Authorization, "Bearer " + token);
  assert.equal(JSON.parse(calls[1].options.body).confirmation, "RECORD-VISUAL-REVIEW");
  assert.equal(calls[1].options.cache, "no-store");
  assert.throws(() => client.visualReviews("../bad", version), /产品编号无效/);
  assert.throws(() => client.submitVisualReview(id, "bad", payload), /产品编号无效/);
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
  assert.equal(calls[0].options.method, "GET");
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
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.headers["Content-Type"], "application/json");
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

test("Commander video approval API requires an exact one-time ticket and supports persisted attempt IDs", async () => {
  const calls = [];
  const requestId = "b".repeat(32);
  const taskId = "a".repeat(32);
  const attemptId = "biz-" + "c".repeat(32);
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      if (String(url).endsWith("/video-approval-requests?limit=12")) {
        return { ok: true, json: async () => ({ api_version: 1, approval_requests: [{ id: taskId, status: "waiting_approval", can_approve: true }] }) };
      }
      if (String(url).endsWith("/video-approval-requests")) {
        return { ok: true, status: 202, json: async () => ({ api_version: 1, approval: { task_id: taskId, status: "waiting_approval" } }) };
      }
      if (String(url).endsWith("/approve")) {
        return { ok: true, status: 202, json: async () => ({ api_version: 1, attempt_id: attemptId, execution_ticket_id: "d".repeat(32) }) };
      }
      return { ok: true, json: async () => ({ api_version: 1, job: { id: attemptId, status: "queued", worker_available: true } }) };
    },
  });
  const payload = { request_id: requestId, title: "AI 自动化", prompt: "真实展示自动化流程" };
  const created = await client.createVideoApprovalRequest(payload);
  const listed = await client.videoApprovalRequests(12);
  const approved = await client.approveVideoApprovalRequest(taskId);
  const job = await client.videoJob(approved.attempt_id);
  assert.equal(created.approval.task_id, taskId);
  assert.equal(listed.approval_requests[0].can_approve, true);
  assert.equal(job.job.id, attemptId);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/video-approval-requests");
  assert.deepEqual(JSON.parse(calls[0].options.body), payload);
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/video-approval-requests?limit=12");
  assert.equal(calls[2].url, "http://127.0.0.1:8765/api/v1/video-approval-requests/" + taskId + "/approve");
  assert.deepEqual(JSON.parse(calls[2].options.body), { confirmation: "APPROVE-VIDEO-ONCE" });
  assert.equal(calls[3].url, "http://127.0.0.1:8765/api/v1/video-jobs/" + attemptId);
  for (const call of calls) assert.equal(call.options.headers.Authorization, "Bearer " + token);
  assert.throws(() => client.createVideoApprovalRequest({ request_id: "../bad" }), /编号无效/);
  assert.throws(() => client.approveVideoApprovalRequest("../bad"), /审批编号无效/);
  assert.throws(() => client.videoJob("biz-../bad"), /任务编号无效/);
  assert.throws(() => client.videoApprovalRequests(101), RangeError);
});

test("visual review API reads append-only history and submits an explicitly confirmed decision", async () => {
  const calls = [];
  const identity = "a".repeat(32);
  const version = "b".repeat(20);
  const record = {
    record_id: "c".repeat(32), decision: "approved",
    reviewer: { label: "Local Editor", role: "video_editor", identity_assurance: "self_asserted_local_session" },
    review_sha256: "d".repeat(64), evidence_status: "verified_human_review_digest_scope_bound",
  };
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => String(url).endsWith("/visual-review") && options.method === "POST"
        ? { api_version: 1, identity_assurance: "self_asserted_local_session", record, append_only: true, digital_signature: false }
        : String(url).endsWith("/revision-brief")
          ? { api_version: 1, active: false, brief: null, can_auto_modify_source: false, new_version_created: false }
          : { api_version: 1, identity_assurance: "self_asserted_local_session", evidence: { video_sha256: "e".repeat(64), artifact_sha256: { "video.mp4": "e".repeat(64), "contact-sheet.png": "f".repeat(64) }, release_manifest_sha256: "1".repeat(64) }, count: 1, reviews: [record] } };
    },
  });
  const history = await client.visualReviews(identity, version);
  assert.equal(history.reviews[0].record_id, record.record_id);
  const brief = await client.visualRevisionBrief(identity, version);
  assert.equal(brief.active, false);
  assert.equal(brief.can_auto_modify_source, false);
  const payload = {
    decision: "approved", reviewer: "Local Editor", reviewer_role: "video_editor",
    rationale: "Reviewed the complete video and the contact sheet carefully.",
    checklist: { subject_framing: true, shot_continuity: true, visual_anomalies: true, subtitle_visibility: true, full_video_playback: true },
    confirmation: "RECORD-VISUAL-REVIEW",
  };
  const saved = await client.submitVisualReview(identity, version, payload);
  assert.equal(saved.append_only, true);
  assert.equal(saved.digital_signature, false);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/products/" + identity + "/" + version + "/visual-review");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/products/" + identity + "/" + version + "/revision-brief");
  assert.equal(calls[1].options.method, "GET");
  assert.equal(calls[1].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[1].options.cache, "no-store");
  assert.equal(calls[2].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[2].options.body), payload);
  assert.throws(() => client.visualReviews("../bad", version), /产品编号无效/);
  assert.throws(() => client.visualRevisionBrief(identity, "../bad"), /产品编号无效/);
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

test("system dashboard uses the authenticated read-only local endpoint", async () => {
  const calls = [];
  const expected = {
    api_version: 1,
    runner: { status: "fresh", project_count: 4, skill_count: 34 },
    files: { status: "available", count: 12, items: [], truncated: false, content_included: false },
    scheduled_tasks: { status: "available", items: [] },
    read_only: true,
  };
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => expected };
    },
  });
  assert.deepEqual(await client.systemDashboard(), expected);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/system-dashboard");
  assert.equal(calls[0].options.method, "GET");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
});

test("contextual inquiry review APIs use authenticated no-store reads and validate identifiers", async () => {
  const calls = [];
  const expected = { api_version: 1, count: 1, reviews: [{ task_id: "a".repeat(32), created_at: "2026-11-21T10:00:00Z", updated_at: "2026-11-21T10:05:00Z" }] };
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => expected };
    },
  });
  assert.deepEqual(await client.contextualInquiryReviews(20), expected);
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/contextual-inquiry-reviews?limit=20");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
  assert.equal(calls[0].options.cache, "no-store");
  await client.contextualInquiryReviewResult("a".repeat(32));
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/contextual-inquiry-reviews/" + "a".repeat(32));
  assert.equal(calls[1].options.redirect, "error");
  assert.throws(() => client.contextualInquiryReviews(101), RangeError);
  assert.throws(() => client.contextualInquiryReviewResult("../bad"), /任务编号无效/);
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

test("skill fusion client validates source and exposes submit, history and approval endpoints", async () => {
  const calls = [];
  const client = createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, jobs: [], fusion: { fusion_id: "a".repeat(32) } }) };
    },
  });
  await client.fusionJobs(7);
  await client.submitFusion({ source_url: "https://github.com/example/project" });
  await client.approveFusion("a".repeat(32));
  await client.rollbackFusion("a".repeat(32));
  assert.equal(calls[0].url, "http://127.0.0.1:8765/api/v1/fusion/jobs?limit=7");
  assert.equal(calls[1].url, "http://127.0.0.1:8765/api/v1/fusion/submit");
  assert.equal(calls[1].options.method, "POST");
  assert.equal(JSON.parse(calls[1].options.body).source_url, "https://github.com/example/project");
  assert.equal(calls[2].url, "http://127.0.0.1:8765/api/v1/fusion/jobs/" + "a".repeat(32) + "/approve");
  assert.equal(JSON.parse(calls[2].options.body).confirmation, "FUSE-CAPABILITIES");
  assert.equal(calls[3].url, "http://127.0.0.1:8765/api/v1/fusion/jobs/" + "a".repeat(32) + "/rollback");
  assert.equal(JSON.parse(calls[3].options.body).confirmation, "ROLLBACK-FUSION");
  assert.throws(() => client.fusionJobs(0), RangeError);
  assert.throws(() => client.submitFusion({ source_url: "https://github.com/example/project", archive_base64: "abc" }), /只提供一个/);
  assert.throws(() => client.approveFusion("invalid"), /编号无效/);
});

test("desktop resolves the same-origin loopback cookie session when Tauri IPC is unavailable", async () => {
  const previousWindow = globalThis.window;
  const origin = "http://127.0.0.1:43212";
  globalThis.window = {
    location: { origin },
    __TAURI__: { core: { invoke: async () => { throw new Error("IPC bridge unavailable on loopback origin"); } } },
  };
  try {
    assert.deepEqual(await resolveDesktopConnection(), { baseUrl: origin, token: "" });
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("cookie-backed desktop API uses same-origin credentials without exposing a bearer token", async () => {
  const previousWindow = globalThis.window;
  const origin = "http://127.0.0.1:43213";
  const calls = [];
  globalThis.window = { location: { origin } };
  try {
    const client = createApiClient({
      baseUrl: origin,
      token: "",
      fetchImpl: async (url, options) => {
        calls.push({ url: String(url), options });
        if (String(url).endsWith("/events/stream")) {
          return { ok: true, headers: { get: () => "text/event-stream" }, text: async () => "" };
        }
        return { ok: true, json: async () => ({ api_version: 1, service: "muse-status-api" }) };
      },
    });
    await client.health();
    await client.streamEvents({ signal: new AbortController().signal, onEvent: () => {} });
    assert.equal(calls.length, 2);
    for (const call of calls) {
      assert.equal(call.options.credentials, "same-origin");
      assert.equal(call.options.headers.Authorization, undefined);
      assert.equal(call.options.cache, "no-store");
    }
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

test("cookie-only desktop API sessions are rejected outside loopback origins", () => {
  const previousWindow = globalThis.window;
  globalThis.window = { location: { origin: "http://192.168.68.173:8766" } };
  try {
    assert.throws(() => createApiClient({
      baseUrl: "http://192.168.68.173:8766",
      token: "",
      fetchImpl: async () => { throw new Error("must not fetch"); },
    }), /本地 API 会话信息无效/);
  } finally {
    if (previousWindow === undefined) delete globalThis.window;
    else globalThis.window = previousWindow;
  }
});

