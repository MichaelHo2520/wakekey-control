/* WakeKey control page service worker: open fast, and open at all on a bad network.
 *
 * - The page itself is network-first: a new version shows on the next open whenever
 *   the network answers within 3 s; otherwise (slow or offline) the copy kept here.
 *   So there is no "stuck on an old version" problem and no version to bump for it.
 * - The pinned CDN scripts and the icons are cache-first (their URLs never change
 *   content). Bump CACHE when the list below changes. */
"use strict";
const CACHE = "wakekey-v1";
const PAGE = "./";
const SHELL = [
  PAGE, "manifest.json", "icon.svg", "icon-192.png", "apple-touch-icon.png",
  "https://cdn.jsdelivr.net/npm/mqtt@5.10.1/dist/mqtt.min.js",
  "https://cdnjs.cloudflare.com/ajax/libs/qrcodejs/1.0.0/qrcode.min.js",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;
  if (req.mode === "navigate") { e.respondWith(page(req)); return; }
  const url = new URL(req.url);
  if (url.origin === location.origin || SHELL.includes(req.url)) e.respondWith(asset(req));
});

async function page(req) {
  const cache = await caches.open(CACHE);
  try {
    const res = await Promise.race([
      fetch(req),
      new Promise((_, reject) => setTimeout(() => reject(new Error("slow")), 3000)),
    ]);
    if (res.ok) cache.put(PAGE, res.clone());
    return res;
  } catch (err) {
    return (await cache.match(PAGE)) || fetch(req);
  }
}

async function asset(req) {
  const hit = await caches.match(req, { ignoreSearch: true });
  if (hit) return hit;
  const res = await fetch(req);
  if (res.ok && new URL(req.url).origin === location.origin) (await caches.open(CACHE)).put(req, res.clone());
  return res;
}
