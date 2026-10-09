/* Offline support. Everything is fetched fresh from the network first, so a new version of the app shows up
   on the next open; the last saved copy is used only when there is no connection. */
const SHELL = 'radar-shell-v3';
const DATA = 'radar-data-v1';
const FILES = ['./', 'index.html', 'app.css', 'app.js', 'workflow.js', 'manifest.webmanifest',
  'icons/icon-192.png', 'icons/icon-512.png', 'icons/apple-touch-icon.png'];

self.addEventListener('install', (e) => {
  // 'reload' skips the browser's own cache, so the saved copy is really the newest one
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES.map((f) => new Request(f, { cache: 'reload' }))))
    .then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== SHELL && k !== DATA).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const req = e.request;
  const url = new URL(req.url);
  if (req.method !== 'GET' || url.origin !== location.origin) return;
  const isData = url.pathname.includes('/data/');
  const key = isData ? url.origin + url.pathname : req;  // data: ignore the cache-busting query
  const store = isData ? DATA : SHELL;
  // navigations must be fetched as they are; other app files ask the server whether they changed
  const net = req.mode === 'navigate' || isData ? fetch(req) : fetch(req.url, { cache: 'no-cache', credentials: 'same-origin' });
  e.respondWith(net.then((r) => {
    if (r.ok) { const copy = r.clone(); caches.open(store).then((c) => c.put(key, copy)); }
    return r;
  }).catch(() => caches.match(key, { ignoreSearch: !isData }).then((r) => r
    || (isData ? new Response('null', { status: 404 }) : caches.match('index.html')))));
});
