// Sound + vibration cues shared by every countdown in the app — the
// workout rest timer (static/js/rest-timer.js) and the guided stretch
// timer (static/js/stretch-timer.js). Extracted from rest-timer.js so
// the hard-won iOS audio-unlock handling below lives in one place
// instead of two copies drifting apart (CLAUDE.md — don't duplicate an
// abstraction that can be shared). Loaded with a plain <script defer>
// before either timer, so createTimerAudio() is just a global.
//
// `muted` persists in localStorage (not a server-side preference — it's
// a device/browser setting, same reasoning as a browser's own volume
// control), under a key each timer chooses, so muting one timer
// doesn't silently mute the other.
function createTimerAudio(muteStorageKey) {
  return {
    muted: localStorage.getItem(muteStorageKey) === "true",
    // One AudioContext, created lazily and reused for the rest of the
    // page's life — see unlock() below for why it can't just be
    // created fresh inside beep() (that was rest-timer.js's original,
    // broken approach: silent on iOS Safari every time).
    ctx: null,
    // Regression (rest-timer.js): a countdown usually finishes and
    // beeps from a setInterval callback, often well after whatever tap
    // started it — not a synchronous user gesture as far as the
    // browser's audio-unlock tracking is concerned. iOS Safari (and
    // other WebKit browsers) refuse to ever produce sound from an
    // AudioContext that was never created/resumed *inside* one.
    // Unlocking on the very first tap/touch anywhere on the page
    // guarantees it happens before any countdown could finish.
    listenForUnlock() {
      const unlock = () => this.unlock();
      document.addEventListener("click", unlock, { once: true });
      document.addEventListener("touchstart", unlock, { once: true });
    },
    unlock() {
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) return;
      if (!this.ctx) {
        this.ctx = new AudioContextClass();
        try {
          // A near-silent (zero-gain) blip scheduled synchronously
          // inside this gesture handler is what actually unlocks
          // playback on iOS Safari — calling resume() alone isn't
          // reliably enough there, only here for belt-and-suspenders.
          const unlockOsc = this.ctx.createOscillator();
          const unlockGain = this.ctx.createGain();
          unlockGain.gain.value = 0;
          unlockOsc.connect(unlockGain);
          unlockGain.connect(this.ctx.destination);
          unlockOsc.start(0);
          unlockOsc.stop(this.ctx.currentTime + 0.01);
        } catch (e) {
          // Fine — beep() still tries resume() again before playing.
        }
      }
      if (this.ctx.state === "suspended") {
        this.ctx.resume();
      }
    },
    toggleMute() {
      this.muted = !this.muted;
      localStorage.setItem(muteStorageKey, this.muted);
    },
    // Web Audio API — a synthesized chime (one tone per entry in
    // `frequencies`, played in sequence), so no audio asset needs
    // shipping/loading. Wrapped in try/catch: a browser without Web
    // Audio just gets the (still very visible) countdown, nothing more.
    beep(frequencies = [880, 1320]) {
      if (this.muted || !this.ctx) return;
      try {
        if (this.ctx.state === "suspended") {
          this.ctx.resume();
        }
        const ctx = this.ctx;
        frequencies.forEach((freq, i) => {
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
    // Vibration API — Android only in practice (iOS Safari doesn't
    // implement it), so strictly an extra alongside beep(), never the
    // only cue. Follows the same mute switch: "muted" means "don't
    // make the phone do anything noticeable".
    vibrate(pattern = [200]) {
      if (this.muted || !("vibrate" in navigator)) return;
      try {
        navigator.vibrate(pattern);
      } catch (e) {
        // Silently skip.
      }
    },
  };
}
