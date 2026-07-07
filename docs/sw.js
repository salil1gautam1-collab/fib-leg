// Minimal service worker — makes the app installable + gives an offline fallback.
// NOTE: shell is NETWORK-FIRST (not cache-first) so a fresh GitHub Pages deploy always
// shows up on the next load; the cache is only a fallback for when you're offline. This
// is what stops the "still seeing the old version" problem.
const CACHE = "fibleg-v94";
const SHELL = ["./", "index.html", "style.css?v=94", "app.js?v=94", "icon.svg", "manifest.webmanifest"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  if (e.request.method !== "GET") return;
  // network-first: always try the live server, refresh the cached copy in the background,
  // and fall back to cache only when the network is unavailable (offline).
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        const copy = resp.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return resp;
      })
      .catch(() => caches.match(e.request))
  );
});
