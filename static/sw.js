const CACHE_NAME = 'family-cctv-static-v3';
const STATIC_ASSETS = [
  '/static/manifest.json',
  '/static/logo.svg',
  '/static/icon-192.png',
  '/static/icon-512.png'
];

self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(CACHE_NAME).then((cache) => cache.addAll(STATIC_ASSETS))
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(
      keys.filter((key) => key !== CACHE_NAME).map((key) => caches.delete(key))
    ))
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== 'GET' || url.origin !== self.location.origin) {
    return;
  }

  if (url.pathname.startsWith('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
    return;
  }

  if (event.request.mode === 'navigate') {
    event.respondWith(fetch(event.request));
  }
});

self.addEventListener("push", (event) => {
  const data = event.data ? event.data.text() : "Motion Detected!";
  event.waitUntil(
    self.registration.showNotification("Family CCTV", {
      body: data,
      icon: "/static/icon-192.png",
      badge: "/static/icon-192.png"
    })
  );
});

