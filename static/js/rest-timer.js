// Training mode's rest timer — plain client-side countdown, no server
// round-trip needed while it runs. The countdown itself is driven
// entirely by `remaining`. `muted` persists in localStorage (not a
// server-side user preference — it's a device/browser setting, same
// reasoning as e.g. a browser's own volume control) so it survives
// across page loads without needing a model field or round trip.
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
    muted: localStorage.getItem(MUTE_STORAGE_KEY) === "true",
    // One AudioContext, created lazily and reused for the rest of the
    // page's life — see init()/unlockAudio() below for why it can't
    // just be created fresh inside beep() (that was the original,
    // broken approach: silent on iOS Safari every time).
    audioCtx: null,
    // Alpine calls init() automatically once this component mounts —
    // no separate x-init="" needed on the element.
    init() {
      // Regression: the countdown usually finishes and calls beep()
      // from a setInterval callback, and often auto-*starts* from an
      // HX-Trigger event handled well after the "Log set" tap that
      // caused it — neither is a synchronous user gesture as far as
      // the browser's audio-unlock tracking is concerned. iOS Safari
      // (and other WebKit browsers) refuse to ever produce sound from
      // an AudioContext that was never created/resumed *inside* one,
      // so a context built fresh at beep()-time there is silently
      // useless. Unlocking on the very first tap/touch anywhere on
      // the page instead guarantees it happens before any rest period
      // could ever finish, since reaching this page at all means the
      // user just tapped something (at minimum "Log set").
      const unlock = () => this.unlockAudio();
      document.addEventListener("click", unlock, { once: true });
      document.addEventListener("touchstart", unlock, { once: true });
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
    unlockAudio() {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      if (!this.audioCtx) {
        this.audioCtx = new AudioContextClass();
        try {
          // A near-silent (zero-gain) blip scheduled synchronously
          // inside this gesture handler is what actually unlocks
          // playback on iOS Safari — calling resume() alone isn't
          // reliably enough there, only here for belt-and-suspenders.
          const unlockOsc = this.audioCtx.createOscillator();
          const unlockGain = this.audioCtx.createGain();
          unlockGain.gain.value = 0;
          unlockOsc.connect(unlockGain);
          unlockGain.connect(this.audioCtx.destination);
          unlockOsc.start(0);
          unlockOsc.stop(this.audioCtx.currentTime + 0.01);
        } catch (e) {
          // Fine — beep() still tries resume() again before playing.
        }
      }
      if (this.audioCtx.state === "suspended") {
        this.audioCtx.resume();
      }
    },
    get formatted() {
      const total = Math.max(0, this.remaining);
      const m = Math.floor(total / 60);
      const s = total % 60;
      return m + ":" + String(s).padStart(2, "0");
    },
    start(seconds) {
      this.unlockAudio();
      clearInterval(this.intervalId);
      this.remaining = seconds;
      this.endAt = Date.now() + seconds * 1000;
      this.running = true;
      this.intervalId = setInterval(() => this.tick(), 1000);
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
    },
    stop() {
      clearInterval(this.intervalId);
      this.running = false;
      this.remaining = 0;
    },
    toggleMute() {
      this.muted = !this.muted;
      localStorage.setItem(MUTE_STORAGE_KEY, this.muted);
    },
    beep() {
      if (this.muted || !this.audioCtx) return;
      // Web Audio API — a synthesized two-tone chime, so no audio
      // asset needs shipping/loading for one short beep. Wrapped in
      // try/catch: a browser that doesn't support Web Audio at all
      // just gets the (still very visible) countdown reaching zero,
      // nothing more.
      try {
        if (this.audioCtx.state === "suspended") {
          this.audioCtx.resume();
        }
        const ctx = this.audioCtx;
        [880, 1320].forEach((freq, i) => {
          const osc = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.connect(gain);
          gain.connect(ctx.destination);
          osc.frequency.value = freq;
          const startAt = ctx.currentTime + i * 0.18;
          gain.gain.setValueAtTime(0.001, startAt);
          gain.gain.exponentialRampToValueAtTime(0.25, startAt + 0.02);
          gain.gain.exponentialRampToValueAtTime(0.001, startAt + 0.35);
          osc.start(startAt);
          osc.stop(startAt + 0.35);
        });
      } catch (e) {
        // Silently skip — see comment above.
      }
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
      navigator.serviceWorker
        .getRegistration()
        .then((registration) => {
          if (!registration) return;
          return registration.showNotification(this.$el.dataset.notifyTitle, {
            body: this.$el.dataset.notifyBody,
            icon: "/static/icons/icon-192.png",
            tag: "ironstack-rest-timer",
          });
        })
        .catch(() => {
          // Silently skip — same reasoning as beep()'s own try/catch.
        });
    },
  };
}
