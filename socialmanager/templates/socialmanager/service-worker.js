self.addEventListener("push", (event) => {
    let data = {};
    try {
        data = event.data ? event.data.json() : {};
    } catch (error) {
        data = { body: event.data ? event.data.text() : "" };
    }
    event.waitUntil(self.registration.showNotification(data.title || "Creana", {
        body: data.body || "You have a new notification.",
        icon: data.icon || "/static/socialmanager/images/icon.png",
        badge: data.badge || "/static/socialmanager/images/icon.png",
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
