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
      this.running = true;
      this.intervalId = setInterval(() => {
        this.remaining -= 1;
        if (this.remaining <= 0) {
          this.finish();
        }
      }, 1000);
    },
    adjust(delta) {
      this.remaining = Math.max(0, this.remaining + delta);
    },
    // Countdown reaching zero on its own — the only path that plays a
    // sound, distinct from a manual "Skip rest" (stop() below), which
    // shouldn't beep since the user is the one ending it.
    finish() {
      clearInterval(this.intervalId);
      this.running = false;
      this.remaining = 0;
      this.beep();
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
  };
}
