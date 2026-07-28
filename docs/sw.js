// Minimal service worker — makes the app installable + gives an offline fallback.
// Shell is NETWORK-FIRST (not cache-first) so a fresh GitHub Pages deploy always shows up
// on the next load; the cache is only a fallback for when you're offline. Data files
// (*.json) are NETWORK-ONLY and never cached — they're uniquely timestamped and refresh
// every 60s, so caching them would both go stale and balloon the cache.
// ONE version constant — must match the ?v= in index.html, else the offline fallback
// precaches URLs the page never requests (query-sensitive cache matching, audit M5)
const V = "151";
const CACHE = "fibleg-v" + V;
const SHELL = ["./", "index.html", `style.css?v=${V}`, `app.js?v=${V}`, "icon.svg", "manifest.webmanifest"];

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
  const url = new URL(e.request.url);
  // live data — always go to the network, never cache; fall back to nothing if offline
  if (url.pathname.endsWith(".json")) {
    e.respondWith(fetch(e.request).catch(() => caches.match(e.request)));
    return;
  }
  // app shell — network-first (fresh deploys win), refresh the cached copy, cache fallback offline
  e.respondWith(
    fetch(e.request)
      .then((resp) => {
        if (resp.ok) {                      // never cache a transient 404 from a mid-deploy Pages
          const copy = resp.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        }
        return resp;
      })
      .catch(() => caches.match(e.request))
  );
});
