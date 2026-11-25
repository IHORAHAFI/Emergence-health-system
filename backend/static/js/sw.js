/* Service worker (served at /sw.js so it controls the whole site).
 * Problem solved: when the network drops, the app still shows the emergency number and the
 * health-information pages already visited, instead of a browser error page.
 * Privacy: only PUBLIC pages are cached (health information, offline page, static files).
 * Personal pages (dashboards, results, messages) and all API/POST requests are never stored. */
const VERSION = 'v2';  // bump to drop old cached files
const CACHE = `medemergency-${VERSION}`;
const PRECACHE = ['/offline/', '/medical/', '/static/css/app.css', '/static/css/design-v2.css', '/static/js/app.js', '/static/img/icon.svg'];
const PUBLIC = [/^\/medical\//, /^\/offline\/$/, /^\/static\//];

self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting())));
self.addEventListener('activate', e => e.waitUntil(
  caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))).then(() => self.clients.claim())));

self.addEventListener('fetch', e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;
  const isPublic = PUBLIC.some(r => r.test(url.pathname));
  if (url.pathname.startsWith('/static/')) {          // static files: network first (design updates show at once), cache when offline
    e.respondWith(fetch(req).then(res => { if (res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); } return res; })
      .catch(() => caches.match(req)));
    return;
  }
  if (req.mode === 'navigate') {                       // pages: network first, offline fallback
    e.respondWith(fetch(req).then(res => {
      if (isPublic && res.ok) { const copy = res.clone(); caches.open(CACHE).then(c => c.put(req, copy)); }
      return res;
    }).catch(() => (isPublic ? caches.match(req) : Promise.resolve(null)).then(hit => hit || caches.match('/offline/'))));
  }
});
