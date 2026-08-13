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
// cache-first strategy. Every other request (pages, forms, HTMX
// responses) always goes straight to the network, untouched.

const STATIC_CACHE = "ironstack-static-v1";

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
    caches.open(STATIC_CACHE).then((cache) =>
      cache.match(event.request).then(
        (cached) =>
          cached ||
          fetch(event.request).then((response) => {
            cache.put(event.request, response.clone());
            return response;
          })
      )
    )
  );
});
