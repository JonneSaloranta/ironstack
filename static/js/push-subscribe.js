// Enable/disable Web Push notifications from Profile → Notifications
// (docs/SECURITY.md "Web Push notifications"). Only ever loaded on
// that page (like barcode-scanner.js, only where it's actually used),
// and only rendered there at all when settings.PUSH_ENABLED is true
// server-side — apps.core.context_processors.push.
//
// The first JS-initiated POST in this codebase — everything else is
// HTMX or a real <form> with {% csrf_token %} — so CSRF needs
// handling by hand here: read the csrftoken cookie Django's own CSRF
// middleware already sets, send it as the X-CSRFToken header its
// same middleware checks for on an AJAX-style request.
function getCsrfCookie() {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function postJSON(url, body) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfCookie() },
    body: JSON.stringify(body),
  });
}

// Standard MDN/web.dev boilerplate: PushManager.subscribe's own
// applicationServerKey wants a Uint8Array, not the base64url string
// VAPID_PUBLIC_KEY actually is.
function urlBase64ToUint8Array(base64String) {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) outputArray[i] = rawData.charCodeAt(i);
  return outputArray;
}

function ironstackPushNotifications() {
  return {
    supported: "serviceWorker" in navigator && "PushManager" in window && "Notification" in window,
    subscribed: false,
    busy: false,
    error: null,

    async init() {
      if (!this.supported) return;
      const registration = await navigator.serviceWorker.ready;
      const subscription = await registration.pushManager.getSubscription();
      this.subscribed = subscription !== null;
    },

    async enable() {
      this.busy = true;
      this.error = null;
      try {
        if (Notification.permission === "denied") {
          throw new Error("Notifications are blocked for this site in your browser settings.");
        }
        const registration = await navigator.serviceWorker.ready;
        const subscription = await registration.pushManager.subscribe({
          userVisibleOnly: true,
          applicationServerKey: urlBase64ToUint8Array(document.body.dataset.vapidPublicKey),
        });
        const response = await postJSON(
          document.body.dataset.pushSubscribeUrl,
          subscription.toJSON()
        );
        if (!response.ok) throw new Error("Saving the subscription failed.");
        this.subscribed = true;
      } catch (e) {
        this.error = e.message || String(e);
      } finally {
        this.busy = false;
      }
    },

    async disable() {
      this.busy = true;
      this.error = null;
      try {
        const registration = await navigator.serviceWorker.ready;
        const subscription = await registration.pushManager.getSubscription();
        if (subscription) {
          await postJSON(document.body.dataset.pushUnsubscribeUrl, {
            endpoint: subscription.endpoint,
          });
          await subscription.unsubscribe();
        }
        this.subscribed = false;
      } catch (e) {
        this.error = e.message || String(e);
      } finally {
        this.busy = false;
      }
    },
  };
}
