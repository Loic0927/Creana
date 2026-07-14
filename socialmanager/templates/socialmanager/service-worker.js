{% load static %}
const CACHE_PREFIX = "creana-static-";
const STATIC_BUILD_URLS = [
    "{% static 'socialmanager/css/socialmanager.css' %}",
    "{% static 'socialmanager/js/socialmanager.js' %}",
];
const STATIC_BUILD_TOKEN = STATIC_BUILD_URLS
    .map((url) => url.match(/\.([0-9a-f]{8,})\.[^.]+$/i)?.[1])
    .filter(Boolean)
    .join("-") || "dev-v1";
const CACHE_NAME = `${CACHE_PREFIX}${STATIC_BUILD_TOKEN}`;
const PRIVATE_PATH_PREFIXES = [
    "/accounts/",
    "/admin/",
    "/api/",
    "/login/",
    "/logout/",
    "/media/",
    "/notifications/",
    "/password-reset/",
    "/profile/",
    "/push/",
    "/settings/",
    "/subscriptions/",
];
const PRIVATE_PATH_PARTS = ["/draft", "/scheduled", "/upload"];
const VERSIONED_STATIC_ASSET = /\.[0-9a-f]{8,}\.(?:css|gif|ico|jpe?g|js|png|svg|webp|woff2?)(?:$|\?)/i;

function isPrivatePath(pathname) {
    return PRIVATE_PATH_PREFIXES.some((prefix) => pathname.startsWith(prefix))
        || PRIVATE_PATH_PARTS.some((part) => pathname.includes(part));
}

function isVersionedStaticAsset(request, url) {
    return request.method === "GET"
        && url.origin === self.location.origin
        && url.pathname.startsWith("/static/")
        && VERSIONED_STATIC_ASSET.test(url.pathname);
}

self.addEventListener("install", (event) => {
    event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
    event.waitUntil((async () => {
        const cacheNames = await caches.keys();
        await Promise.all(
            cacheNames
                .filter((name) => name.startsWith(CACHE_PREFIX) && name !== CACHE_NAME)
                .map((name) => caches.delete(name)),
        );
        await self.clients.claim();
    })());
});

self.addEventListener("message", (event) => {
    if (event.data?.type === "SKIP_WAITING") {
        self.skipWaiting();
    }
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    const url = new URL(request.url);

    if (request.method !== "GET" || url.origin !== self.location.origin || isPrivatePath(url.pathname)) {
        return;
    }

    // HTML is always fetched from the network and is never written to Cache Storage.
    // This prevents authenticated or user-specific pages from appearing offline or stale.
    if (request.mode === "navigate") {
        event.respondWith(fetch(new Request(request, { cache: "no-store" })));
        return;
    }

    // Django's manifest storage fingerprints production assets. Only those immutable
    // files use cache-first; unversioned static files and all application endpoints
    // remain network-only.
    if (isVersionedStaticAsset(request, url)) {
        event.respondWith((async () => {
            const cached = await caches.match(request);
            if (cached) return cached;

            const response = await fetch(request);
            const cacheControl = response.headers.get("Cache-Control") || "";
            if (
                response.ok
                && response.type === "basic"
                && !/\b(?:private|no-store)\b/i.test(cacheControl)
            ) {
                const cache = await caches.open(CACHE_NAME);
                await cache.put(request, response.clone());
            }
            return response;
        })());
    }
});

self.addEventListener("push", (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (error) {
        data = { body: event.data ? event.data.text() : "" };
    }
    event.waitUntil(self.registration.showNotification(data.title || "Creana", {
        body: data.body || "You have a new notification.",
        icon: data.icon || "{% url 'pwa_icon' filename='pwa-icon-192.png' %}",
        badge: data.badge || "{% url 'pwa_icon' filename='pwa-icon-192.png' %}",
        data: { url: data.url || "/notifications/" },
    }));
});

self.addEventListener("notificationclick", (event) => {
    event.notification.close();
    const targetUrl = new URL(event.notification.data?.url || "/notifications/", self.location.origin).href;
    event.waitUntil(clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
        for (const client of windows) {
            if (client.url === targetUrl && "focus" in client) return client.focus();
        }
        return clients.openWindow ? clients.openWindow(targetUrl) : undefined;
    }));
});
