const CACHE = "muse-portal-shell-v6";
const SHELL = [
  "/", "/styles.css", "/app.js", "/manifest.webmanifest", "/icon.svg",
  "/apple-touch-icon.png", "/icon-192.png", "/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.addAll(SHELL))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((names) => Promise.all(names
        .filter((name) => name.startsWith("muse-portal-shell-") && name !== CACHE)
        .map((name) => caches.delete(name))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || url.pathname.startsWith("/api/")
      || event.request.method !== "GET" || !SHELL.includes(url.pathname)) return;

  const networkResponse = fetch(event.request);
  event.waitUntil(
    networkResponse
      .then((response) => {
        if (!response.ok) return;
        return caches.open(CACHE).then((cache) => cache.put(event.request, response.clone()));
      })
      .catch(() => {})
  );
  event.respondWith(
    networkResponse
      .catch(() => caches.match(event.request))
      .then((response) => response || new Response("Muse is offline. Reconnect to continue.", {
        status: 503,
        headers: { "Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-store" },
      }))
  );
});
