// The version comes from the address this worker was registered under (sw.js?v=...), so nothing here is edited per release.
const VERSION = new URL(self.location).searchParams.get('v') || 'dev';
const CACHE = 'pool-shell-' + VERSION;
const SHELL = ['./', ...['theme.js', 'style.css', 'app.js', 'strip.js', 'color.js'].map(file => `v/${VERSION}/${file}`), 'icon.svg', 'icon-192.png', 'icon-512.png', 'manifest.webmanifest'];
self.addEventListener('install', event => event.waitUntil(caches.open(CACHE).then(cache => cache.addAll(SHELL)).then(() => self.skipWaiting())));
self.addEventListener('activate', event => event.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));
// Network first, falling back to the cached shell; API calls are never cached.
self.addEventListener('fetch', event => {
  const url = new URL(event.request.url);
  if (url.origin !== self.location.origin || event.request.method !== 'GET' || url.pathname.includes('/api/')) return;
  event.respondWith(fetch(event.request).then(response => {
    if (response.ok) { const copy = response.clone(); event.waitUntil(caches.open(CACHE).then(c => c.put(event.request, copy))); }
    return response;
  }).catch(() => caches.match(event.request).then(hit => hit || (event.request.mode === 'navigate' ? caches.match('./') : Response.error()))));
});
