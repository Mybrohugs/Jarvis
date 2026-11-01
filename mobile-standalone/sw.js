'use strict';
// Caches only this app's own files, so it opens offline. Requests to Claude, Gemini, Google and the PC
// are never cached.
const CACHE = 'jarvis-mobile-v15';
const SHELL = ['./', 'index.html', 'app.css', 'mobile.css', 'orb.js', 'main.js', 'brain.js', 'voice.js', 'google.js', 'pc.js',
  'store.js', 'manifest.webmanifest', 'icon-180.png', 'icon-512.png', 'fonts/exo2-cyrillic.woff2', 'fonts/exo2-latin.woff2'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});
self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});
self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) { return; }
  e.respondWith(fetch(e.request).then((r) => {  // network first, so updates arrive at once
    const copy = r.clone();
    caches.open(CACHE).then((c) => c.put(e.request, copy));
    return r;
  }).catch(() => caches.match(e.request)));
});

// Web Push from the paired PC (reminders, briefings). The payload is end-to-end encrypted; shown as text only.
self.addEventListener('push', (e) => {
  let d = { title: 'JARVIS', body: '' };
  try { d = Object.assign(d, e.data ? e.data.json() : {}); } catch (err) { d.body = e.data ? e.data.text() : ''; }
  e.waitUntil(self.registration.showNotification(String(d.title).slice(0, 80), {
    body: String(d.body).slice(0, 300), icon: 'icon-180.png', badge: 'icon-180.png',
  }));
});
self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  e.waitUntil(self.clients.matchAll({ type: 'window' }).then((list) => {
    for (const c of list) { if ('focus' in c) { return c.focus(); } }
    return self.clients.openWindow('./');
  }));
});
