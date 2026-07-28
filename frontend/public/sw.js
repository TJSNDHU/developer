/**
 * sw.js — AUREM CTO Service Worker
 *
 * Strategy:
 *   • App shell (HTML + favicons + manifest + webp bg)    → precache
 *   • Static assets (JS/CSS chunks)                       → stale-while-revalidate
 *   • Backend API (/api/aurem-dev/*)                      → network-only (never stale)
 *   • Everything else                                     → network with cache fallback
 *
 * Versioning: bump CACHE_VERSION on shipped changes so old caches purge.
 */
const CACHE_VERSION = "aurem-v3";
const STATIC_CACHE  = `${CACHE_VERSION}-static`;
const RUNTIME_CACHE = `${CACHE_VERSION}-runtime`;

const APP_SHELL = [
  "/",
  "/site.webmanifest",
  "/favicon.ico",
  "/favicon-32.png",
  "/favicon-192.png",
  "/favicon-512.png",
  "/apple-touch-icon.png",
  "/aurem-bg.webp",
  "/aurem-bg-mobile.webp",
  "/og-image.jpg",
];

self.addEventListener("install", (event) => {
  self.skipWaiting();
  event.waitUntil(
    caches.open(STATIC_CACHE).then((c) =>
      c.addAll(APP_SHELL).catch(() => null)
    )
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((k) => !k.startsWith(CACHE_VERSION))
          .map((k) => caches.delete(k))
      )
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;

  const url = new URL(req.url);

  // Never cache API calls — chat/SSE/auth must hit the live backend.
  if (url.pathname.startsWith("/api/")) return;

  // Never intercept cross-origin requests we don't control.
  if (url.origin !== self.location.origin) return;

  // SSE / event-stream is incompatible with cache.
  if (req.headers.get("accept")?.includes("text/event-stream")) return;

  // Navigation requests → network-first with offline shell fallback.
  if (req.mode === "navigate") {
    event.respondWith(
      fetch(req)
        .then((res) => {
          const copy = res.clone();
          caches.open(RUNTIME_CACHE).then((c) => c.put(req, copy)).catch(() => null);
          return res;
        })
        .catch(() =>
          caches.match(req).then((m) => m || caches.match("/"))
        )
    );
    return;
  }

  // Static assets → stale-while-revalidate.
  if (
    url.pathname.endsWith(".js")  ||
    url.pathname.endsWith(".css") ||
    url.pathname.endsWith(".woff2") ||
    url.pathname.endsWith(".webp") ||
    url.pathname.endsWith(".png")  ||
    url.pathname.endsWith(".jpg")  ||
    url.pathname.endsWith(".svg")  ||
    url.pathname.endsWith(".ico")  ||
    url.pathname.endsWith(".webmanifest")
  ) {
    event.respondWith(
      caches.match(req).then((cached) => {
        const network = fetch(req).then((res) => {
          if (res && res.status === 200) {
            const copy = res.clone();
            caches.open(STATIC_CACHE).then((c) => c.put(req, copy)).catch(() => null);
          }
          return res;
        }).catch(() => cached);
        return cached || network;
      })
    );
    return;
  }

  // Default → network with cache fallback.
  event.respondWith(
    fetch(req).catch(() => caches.match(req))
  );
});

// Allow the app to ask the SW to skip waiting (e.g. after a deploy banner).
self.addEventListener("message", (event) => {
  if (event.data === "SKIP_WAITING") self.skipWaiting();
});
