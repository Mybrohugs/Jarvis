'use strict';
// Caches only the app shell. API responses (/api/*) are never cached.
const CACHE = 'jarvis-shell-v8';

// Feature 3: Web Push. Payload is end-to-end encrypted; shown as text only.
self.addEventListener('push', (e) => {
  let d = { title: 'JARVIS', body: '', url: '/' };
  try { d = Object.assign(d, e.data ? e.data.json() : {}); } catch (err) { d.body = e.data ? e.data.text() : ''; }
  e.waitUntil(self.registration.showNotification(String(d.title).slice(0, 80), {
    body: String(d.body).slice(0, 300), icon: '/icon-180.png', badge: '/icon-180.png',
    data: { url: typeof d.url === 'string' && d.url.startsWith('/') ? d.url : '/' },
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const url = (e.notification.data && e.notification.data.url) || '/';
  e.waitUntil(self.clients.matchAll({ type: 'window' }).then((list) => {
    for (const c of list) { if ('focus' in c) { c.navigate(url); return c.focus(); } }
    return self.clients.openWindow(url);
  }));
});
const SHELL = ['/', '/app.js', '/app.css', '/manifest.webmanifest', '/icon-180.png', '/icon-512.png',
  '/fonts/exo2-cyrillic.woff2', '/fonts/exo2-latin.woff2'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin || url.pathname.startsWith('/api/')) {
    return;
  }
  // Network first so updates arrive; shell from cache when offline.
  e.respondWith(fetch(e.request).then((r) => {
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request)));
});
