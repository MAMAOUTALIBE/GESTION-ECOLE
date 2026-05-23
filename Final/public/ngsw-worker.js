// Self-destruct service worker.
// Purpose: replace any previously installed PWA service worker on this origin
// (127.0.0.1:4200 / localhost:4200) so the cached pages of the old SNBGEE-GN
// app stop being served. Browsers always fetch SW scripts from the network to
// detect updates, so dropping this file at the same URL is enough to evict
// the previous worker on the next page load.
self.addEventListener('install', (event) => {
  // Activate immediately, don't wait for tabs to close.
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    // 1. Wipe every Cache Storage bucket the previous SW had populated.
    const keys = await caches.keys();
    await Promise.all(keys.map((k) => caches.delete(k)));

    // 2. Take control of any open tabs and force them to re-navigate so the
    //    fresh HTML (served by our Angular dev server) replaces the cached one.
    await self.clients.claim();
    const clients = await self.clients.matchAll({ type: 'window' });
    for (const client of clients) {
      try { client.navigate(client.url); } catch (_) { /* cross-origin navigation blocked, ignore */ }
    }

    // 3. Unregister this very worker so future loads are SW-free.
    await self.registration.unregister();
  })());
});

// While we're alive, never serve from cache — always go to network.
self.addEventListener('fetch', (event) => {
  // Don't intercept cross-origin or non-HTTP requests.
  if (!event.request.url.startsWith(self.registration.scope)) return;
  event.respondWith(fetch(event.request));
});
