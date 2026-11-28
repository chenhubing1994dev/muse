import test from "node:test";
import assert from "node:assert/strict";
import { createApiClient } from "./api.js";

const token = "commander-test-token-that-is-long-enough";
function client(calls) {
  return createApiClient({
    baseUrl: "http://127.0.0.1:8765", token,
    fetchImpl: async (url, options) => {
      calls.push({ url: String(url), options });
      return { ok: true, json: async () => ({ api_version: 1, status: {}, approvals: [], adapters: [], result: {}, task: {} }) };
    },
  });
}

test("Commander client hits only authenticated commander routes with explicit confirmations", async () => {
  const calls = [];
  const c = client(calls);
  const id = "a".repeat(32);
  await c.commanderStatus();
  await c.commanderApprovals();
  await c.commanderAdapters();
  await c.commanderApprove(id, "user:owner");
  await c.commanderCancel(id, "human:owner", "no");
  await c.commanderKill("user:owner", "stop");
  assert.deepEqual(calls.map((x) => x.url.replace("http://127.0.0.1:8765", "")), [
    "/api/v1/commander/status", "/api/v1/commander/approvals", "/api/v1/commander/adapters",
    "/api/v1/commander/approve", "/api/v1/commander/cancel", "/api/v1/commander/kill",
  ]);
  assert.equal(calls[3].options.method, "POST");
  assert.deepEqual(JSON.parse(calls[3].options.body), { task_id: id, approved_by: "user:owner", confirmation: "APPROVE" });
  assert.equal(JSON.parse(calls[5].options.body).confirmation, "KILL");
  assert.equal(calls[0].options.headers.Authorization, "Bearer " + token);
});

test("Commander client refuses non-human identities and malformed ticket ids before any request", () => {
  const calls = [];
  const c = client(calls);
  assert.throws(() => c.commanderApprove("a".repeat(32), "agent:self"));
  assert.throws(() => c.commanderApprove("not-an-id", "user:owner"));
  assert.throws(() => c.commanderKill("worker", "x"));
  assert.throws(() => c.commanderCancel("a".repeat(32), "agent:x"));
  assert.equal(calls.length, 0);
});
