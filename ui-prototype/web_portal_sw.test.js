import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import vm from "node:vm";

const source = await readFile(new URL("../web-portal/sw.js", import.meta.url), "utf8");

function createWorkerHarness({ fetchImpl, cachedResponse } = {}) {
  const listeners = new Map();
  const calls = { addAll: [], deleted: [], put: [], matched: [], skipWaiting: 0, clientsClaim: 0 };
  const cache = {
    async addAll(paths) { calls.addAll.push([...paths]); },
    async put(request, response) { calls.put.push({ url: request.url, response }); },
  };
  const context = {
    URL,
    Response,
    self: {
      location: { origin: "https://muse.example.com" },
      clients: { async claim() { calls.clientsClaim += 1; } },
      async skipWaiting() { calls.skipWaiting += 1; },
      addEventListener(type, handler) { listeners.set(type, handler); },
    },
    caches: {
      async open(name) { calls.cacheName = name; return cache; },
      async keys() { return ["muse-portal-shell-v2", "other-app-cache", "muse-portal-shell-v3", "muse-portal-shell-v4"]; },
      async delete(name) { calls.deleted.push(name); return true; },
      async match(request) { calls.matched.push(request.url); return cachedResponse; },
    },
    fetch: fetchImpl || (async () => ({ ok: true, clone() { return this; } })),
  };
  vm.runInNewContext(source, context, { filename: "web-portal/sw.js" });
  return { listeners, calls };
}

test("PWA service worker precaches the app shell during install", async () => {
  const { listeners, calls } = createWorkerHarness();
  assert.equal(typeof listeners.get("install"), "function");
  let installPromise;
  listeners.get("install")({ waitUntil(promise) { installPromise = promise; } });
  await installPromise;
  assert.equal(calls.cacheName, "muse-portal-shell-v5");
  assert.deepEqual(calls.addAll[0], ["/", "/styles.css", "/app.js", "/manifest.webmanifest", "/icon.svg", "/apple-touch-icon.png", "/icon-192.png", "/icon-512.png"]);
  assert.equal(calls.skipWaiting, 1);
});

test("PWA service worker removes only obsolete Muse shell caches on activation", async () => {
  const { listeners, calls } = createWorkerHarness();
  assert.equal(typeof listeners.get("activate"), "function");
  let activationPromise;
  listeners.get("activate")({ waitUntil(promise) { activationPromise = promise; } });
  await activationPromise;
  assert.deepEqual(calls.deleted, ["muse-portal-shell-v2", "muse-portal-shell-v3", "muse-portal-shell-v4"]);
  assert.equal(calls.clientsClaim, 1);
});
test("PWA service worker caches successful shell responses with event lifetime protection", async () => {
  const networkResponse = { ok: true, clone() { return { cached: true }; } };
  const { listeners, calls } = createWorkerHarness({ fetchImpl: async () => networkResponse });
  const waits = [];
  let responsePromise;
  listeners.get("fetch")({
    request: { url: "https://muse.example.com/app.js", method: "GET" },
    waitUntil(promise) { waits.push(promise); },
    respondWith(promise) { responsePromise = promise; },
  });
  assert.equal(await responsePromise, networkResponse);
  assert.equal(waits.length, 1);
  await Promise.all(waits);
  assert.equal(calls.put.length, 1);
  assert.equal(calls.put[0].url, "https://muse.example.com/app.js");
});

test("PWA service worker never intercepts API or cross-origin requests", () => {
  const { listeners } = createWorkerHarness();
  for (const url of [
    "https://muse.example.com/api/v1/portal/overview",
    "https://other.example.com/app.js",
  ]) {
    let intercepted = false;
    listeners.get("fetch")({
      request: { url, method: "GET" },
      respondWith() { intercepted = true; },
      waitUntil() {},
    });
    assert.equal(intercepted, false);
  }
});

test("PWA service worker serves the cached shell when the network is unavailable", async () => {
  const cached = { cached: true };
  const { listeners, calls } = createWorkerHarness({
    fetchImpl: async () => { throw new Error("offline"); },
    cachedResponse: cached,
  });
  let responsePromise;
  listeners.get("fetch")({
    request: { url: "https://muse.example.com/app.js", method: "GET" },
    waitUntil() {},
    respondWith(promise) { responsePromise = promise; },
  });
  assert.equal(await responsePromise, cached);
  assert.equal(calls.matched.length, 1);
});
