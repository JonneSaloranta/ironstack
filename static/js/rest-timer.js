// Training mode's rest timer — plain client-side countdown, no server
// round-trip needed while it runs. The countdown itself is driven
// entirely by `remaining`. Sound (and the mute switch) comes from the
// shared static/js/timer-audio.js, which must load before this file.
//
// Extracted out of templates/workouts/session_train.html into its own
// file so it can be loaded via <script src>, not inline — see
// static/js/sw-register.js's own comment for why (CSP's script-src
// here has no 'unsafe-inline'). Contains no server-rendered value, so
// the move is otherwise a plain copy/paste.
const MUTE_STORAGE_KEY = "ironstack-rest-timer-muted";

function ironstackRestTimer() {
  return {
    remaining: 0,
    running: false,
    intervalId: null,
    // Wall-clock deadline the countdown ticks towards, instead of just
    // decrementing `remaining` by 1 on every setInterval callback. A
    // locked phone (iOS Safari and most mobile Chrome alike) freezes
    // JS timers entirely rather than merely slowing them down, so a
    // decrement-based countdown loses every second the screen was
    // locked — reopening the app resumed counting down from wherever
    // it had frozen, showing far more time left than had actually
    // passed. Deriving `remaining` from `endAt - Date.now()` on every
    // tick (see tick() below) instead means the very next tick that
    // does run — even one long-delayed by a locked screen — catches
    // straight up to how much time has really elapsed.
    endAt: 0,
    audio: createTimerAudio(MUTE_STORAGE_KEY),
    get muted() {
      return this.audio.muted;
    },
    // Alpine calls init() automatically once this component mounts —
    // no separate x-init="" needed on the element.
    init() {
      // See timer-audio.js's listenForUnlock() for why this has to
      // happen on the page's very first tap, not when a rest starts.
      this.audio.listenForUnlock();
      // Belt-and-suspenders for the same locked-screen freeze described
      // at endAt's own comment above: a still-running setInterval isn't
      // guaranteed to fire its callback the instant the screen unlocks
      // (mobile browsers resume a backgrounded tab's timers on their
      // own schedule, not necessarily synchronously with the unlock).
      // Page Visibility firing "visible" is the reliable signal that
      // the app is back in front of the user, so re-derive `remaining`
      // right then rather than waiting for whatever the next natural
      // tick happens to be.
      document.addEventListener("visibilitychange", () => {
        if (!document.hidden && this.running) this.tick();
      });
    },
    get formatted() {
      const total = Math.max(0, this.remaining);
      const m = Math.floor(total / 60);
      const s = total % 60;
      return m + ":" + String(s).padStart(2, "0");
    },
    start(seconds) {
      this.audio.unlock();
      clearInterval(this.intervalId);
      this.remaining = seconds;
      this.endAt = Date.now() + seconds * 1000;
      this.running = true;
      this.intervalId = setInterval(() => this.tick(), 1000);
      this.scheduleServerNotification(seconds);
    },
    // Re-derives `remaining` from the wall-clock deadline rather than
    // trusting that exactly one second passed since the last tick —
    // see endAt's own comment for why that trust doesn't hold on a
    // locked phone. Shared by the interval (the normal path) and the
    // visibilitychange catch-up in init() above.
    tick() {
      this.remaining = Math.max(0, Math.round((this.endAt - Date.now()) / 1000));
      if (this.remaining <= 0) {
        this.finish();
      }
    },
    adjust(delta) {
      this.endAt += delta * 1000;
      this.remaining = Math.max(0, Math.round((this.endAt - Date.now()) / 1000));
      // Re-schedule (not cancel-then-schedule): scheduleServerNotification
      // itself update_or_creates, so this just moves the same pending
      // row's fire_at to match the countdown's own new deadline.
      this.scheduleServerNotification(this.remaining);
    },
    // Countdown reaching zero on its own — the only path that plays a
    // sound (or shows a notification, below), distinct from a manual
    // "Skip rest" (stop() below), which shouldn't do either since the
    // user is the one ending it.
    finish() {
      clearInterval(this.intervalId);
      this.running = false;
      this.remaining = 0;
      this.beep();
      this.notify();
      // The client got here on its own, so it's not frozen right now
      // — cancel the server-side backstop (scheduleServerNotification
      // below) so a real push doesn't arrive moments later as a
      // redundant duplicate of the notify() call just above.
      this.cancelServerNotification();
    },
    stop() {
      clearInterval(this.intervalId);
      this.running = false;
      this.remaining = 0;
      this.cancelServerNotification();
    },
    toggleMute() {
      this.audio.toggleMute();
    },
    beep() {
      this.audio.beep();
    },
    // A system notification for whenever the beep alone might not
    // actually be *noticed* — the phone locked, or a different app/
    // tab in front, mid-rest. Only when the page genuinely isn't the
    // thing on screen right now (document.hidden — Page Visibility
    // API): a visible countdown reaching 0:00 plus the beep above
    // already says "rest's over" clearly enough while looking
    // straight at it, so this would just be a redundant second alert.
    // Never requests permission itself — Notification.permission is
    // only ever "granted" here if the user already turned on push
    // notifications elsewhere (Profile -> Notifications), the same
    // subscribe flow (static/js/push-subscribe.js) that's the one
    // place this app ever actually asks; firing a *second*,
    // surprise permission prompt mid-rest, unprompted, would be
    // exactly the kind of automation taking control away from the
    // user CLAUDE.md's own product principle rules out.
    notify() {
      if (!document.hidden) return;
      if (!("Notification" in window) || Notification.permission !== "granted") return;
      if (!("serviceWorker" in navigator)) return;
      // Regression: `new Notification(...)` — the plain constructor —
      // is what this used to call directly. That's silently a no-op on
      // iOS/iPadOS Safari: WebKit only ever displays a notification
      // triggered through a ServiceWorkerRegistration (either from a
      // "push" event, as static/sw.js's own handler does, or — this
      // case — a direct showNotification() call from page script), and
      // either throws or never renders anything for the bare
      // constructor. static/js/push-subscribe.js's `enable()` already
      // registers this same service worker as part of turning push
      // notifications on in the first place, so it's always present by
      // the time Notification.permission could ever be "granted" here.
      //
      // Regression: reading this.$el.dataset.* *inside* the .then()
      // callback below showed a literal "undefined" title/body in the
      // actual notification — read them here instead, synchronously,
      // while this is unambiguously still the call finish() just made
      // on this component's own instance, and pass the plain strings
      // into the promise chain instead of reaching for `this` again
      // once it's resumed later as a microtask.
      const title = this.$el.dataset.notifyTitle || "IronStack";
      const body = this.$el.dataset.notifyBody || "";
      navigator.serviceWorker
        .getRegistration()
        .then((registration) => {
          if (!registration) return;
          return registration.showNotification(title, {
            body,
            icon: "/static/icons/icon-192.png",
            tag: "ironstack-rest-timer",
          });
        })
        .catch(() => {
          // Silently skip — same reasoning as beep()'s own try/catch.
        });
    },
    // The reliable backstop for notify() above: that one only ever
    // fires if this page's own JS is still running when the countdown
    // reaches zero, which iOS Safari doesn't guarantee once the tab
    // is merely backgrounded, let alone once the screen locks
    // (docs/DEVELOPMENT_LOG.md "A phone-testing pass"). Asking the
    // server (apps.workouts.views.RestTimerScheduleView) to send a
    // real Web Push at the right moment instead works regardless of
    // whether this page's JS ever gets to run again before then — the
    // OS delivers it either way. Same permission gate as notify():
    // never requests it, only ever runs once the user already opted
    // in elsewhere (Profile → Notifications), and silently a no-op
    // otherwise — starting/adjusting a rest period must never *cost*
    // anything for someone who hasn't turned notifications on.
    // Fire-and-forget: a failed request here (offline, server hiccup)
    // must never block or interrupt the countdown itself, which is
    // exactly why this isn't awaited by start()/adjust() above.
    scheduleServerNotification(seconds) {
      if (!("Notification" in window) || Notification.permission !== "granted") return;
      postJSON(this.$el.dataset.restTimerScheduleUrl, {
        seconds,
        title: this.$el.dataset.notifyTitle,
        body: this.$el.dataset.notifyBody,
        url: window.location.href,
      }).catch(() => {
        // Silently skip — same reasoning as notify()'s own try/catch.
      });
    },
    cancelServerNotification() {
      if (!("Notification" in window) || Notification.permission !== "granted") return;
      postJSON(this.$el.dataset.restTimerCancelUrl, {}).catch(() => {
        // Silently skip — same reasoning as notify()'s own try/catch.
      });
    },
  };
}
