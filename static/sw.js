// Minimal PWA service worker for IronStack.
//
// Deliberately NOT an offline-first data cache: this app is a training
// log where historical accuracy matters (CLAUDE.md — "Workout history
// must remain historically trustworthy"). Caching page or HTMX
// responses risks showing stale numbers, or letting a user believe a
// logged set saved when it never reached the server while offline. This
// worker exists only to (a) make the app installable — a fetch handler
// is part of most browsers' installability criteria — and (b) speed up
// repeat loads of genuinely static assets (CSS/JS/icons) via a
// stale-while-revalidate strategy. Every other request (pages, forms,
// HTMX responses) always goes straight to the network, untouched.
//
// Regression fixed here: this used to be pure cache-first — once an
// asset (e.g. base.css) was cached, it was served from that cache
// *forever*, with the network never consulted again for it at all.
// Static files here aren't served at content-hashed URLs (no
// ManifestStaticFilesStorage), so every later CSS/JS fix was
// permanently invisible to any browser that had already cached the old
// version — including, concretely, a whole session's worth of chart/
// nav/layout CSS fixes never reaching a user whose phone had cached
// base.css before any of them shipped. See the fetch handler below.

// Bumped alongside the fetch handler's fix below (v1 -> v2) so every
// existing installation discards its old cache once on update, rather
// than only self-healing gradually as each individual asset happens to
// get re-requested.
const STATIC_CACHE = "ironstack-static-v2";

self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(keys.filter((key) => key !== STATIC_CACHE).map((key) => caches.delete(key)))
      )
      .then(() => self.clients.claim())
  );
});

function isStaticAsset(url) {
  return url.pathname.startsWith("/static/");
}

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  if (event.request.method !== "GET" || !isStaticAsset(url)) {
    return; // not handled here -> browser falls through to the network
  }

  event.respondWith(
    caches.open(STATIC_CACHE).then(async (cache) => {
      const cached = await cache.match(event.request);
      if (cached) {
        // Serve the cached copy immediately — fast, and works offline —
        // but always refetch in the background too and update the
        // cache for the *next* request. event.waitUntil keeps the
        // worker alive long enough for that background refetch to
        // finish even though the response has already gone out.
        event.waitUntil(
          fetch(event.request)
            .then((response) => cache.put(event.request, response))
            .catch(() => {}) // offline — next request just serves the same cached copy again
        );
        return cached;
      }
      // Nothing cached yet — fetch from the network and cache it for
      // next time, same as before for a first-ever request.
      const response = await fetch(event.request);
      cache.put(event.request, response.clone());
      return response;
    })
  );
});
