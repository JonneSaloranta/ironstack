// Guided stretching session player (templates/stretching/session_play.html).
//
// Plays the step list apps.stretching.services.timer_steps() builds on
// the server — "prepare" (get into position), "hold", and "rest" steps
// — one countdown after another, with a chime + vibration at each
// change. The sequencing rules live (and are tested) in Python; this
// file only plays them. When the last hold of a stretch finishes, it
// POSTs that stretch as done (with the seconds actually held) so a page
// reload mid-session resumes from the next pending stretch.
//
// The user stays in charge throughout: pause, ±10 s, skip a step, skip
// a whole stretch — and the plain per-stretch Done/Skip forms under the
// player work without any of this JS at all.
//
// Needs static/js/csrf.js (postJSON) and static/js/timer-audio.js
// (createTimerAudio) loaded first. Same wall-clock-deadline countdown
// as static/js/rest-timer.js — see that file's `endAt` comment for why
// a plain decrement-per-tick drifts on a locked phone.
const STRETCH_TIMER_MUTE_KEY = "ironstack-stretch-timer-muted";

function ironstackStretchTimer() {
  return {
    steps: [],
    index: 0,
    started: false,
    running: false,
    finished: false,
    remaining: 0,
    // Planned length of the current step including any ±10 s
    // adjustments, so the seconds actually held can be worked out as
    // `stepLength - remaining` however the step ended.
    stepLength: 0,
    endAt: 0,
    intervalId: null,
    // performed-stretch pk → seconds held so far, across its holds.
    held: {},
    wakeLock: null,
    audio: createTimerAudio(STRETCH_TIMER_MUTE_KEY),

    init() {
      this.steps = JSON.parse(document.getElementById("stretch-timer-steps").textContent);
      this.finished = this.steps.length === 0;
      this.audio.listenForUnlock();
      document.addEventListener("visibilitychange", () => {
        if (document.hidden) return;
        if (this.running) this.tick();
        // The browser drops a screen wake lock whenever the page is
        // hidden; take it again on the way back if still playing.
        if (this.started && !this.finished) this.requestWakeLock();
      });
    },

    get current() {
      return this.steps[this.index] || null;
    },
    get currentPerformed() {
      return this.current ? this.current.performed : null;
    },
    // The next *different* stretch, for the "Next up" line.
    get nextPerformed() {
      const now = this.currentPerformed;
      for (let i = this.index + 1; i < this.steps.length; i++) {
        if (this.steps[i].performed !== now) return this.steps[i].performed;
      }
      return null;
    },
    get formatted() {
      const total = Math.max(0, this.remaining);
      return Math.floor(total / 60) + ":" + String(total % 60).padStart(2, "0");
    },
    get progress() {
      if (!this.stepLength) return 0;
      return Math.min(100, Math.max(0, ((this.stepLength - this.remaining) / this.stepLength) * 100));
    },
    get phaseLabel() {
      const step = this.current;
      if (!step) return "";
      const data = this.$root.dataset;
      const phase = { prepare: data.labelPrepare, hold: data.labelHold, rest: data.labelRest }[step.kind];
      const parts = [phase];
      if (step.side) parts.push(step.side === "left" ? data.labelLeft : data.labelRight);
      if (step.sets > 1) parts.push(data.labelSet.replace("{set}", step.set).replace("{sets}", step.sets));
      return parts.join(" · ");
    },

    start() {
      this.audio.unlock();
      this.started = true;
      this.requestWakeLock();
      this.beginStep();
    },
    beginStep() {
      clearInterval(this.intervalId);
      const step = this.current;
      if (!step) {
        this.complete();
        return;
      }
      this.stepLength = step.seconds;
      this.remaining = step.seconds;
      this.endAt = Date.now() + step.seconds * 1000;
      this.running = true;
      this.intervalId = setInterval(() => this.tick(), 250);
    },
    tick() {
      this.remaining = Math.max(0, Math.ceil((this.endAt - Date.now()) / 1000));
      if (this.remaining <= 0) this.endStep(true);
    },
    // `natural` = the countdown reached zero by itself (cue the user);
    // a manual skip ends the step silently, same as rest-timer.js.
    endStep(natural) {
      clearInterval(this.intervalId);
      const step = this.current;
      if (step.kind === "hold") {
        this.held[step.performed] = (this.held[step.performed] || 0) + (this.stepLength - this.remaining);
      }
      if (natural) {
        // A hold starting is the cue that matters most — a higher,
        // double chime; everything else gets a single soft one.
        const next = this.steps[this.index + 1];
        if (next && next.kind === "hold") {
          this.audio.beep([988, 1319]);
          this.audio.vibrate([150, 80, 150]);
        } else {
          this.audio.beep([660]);
          this.audio.vibrate([200]);
        }
      }
      if (step.last_of_stretch) this.mark(step.performed, "done");
      this.index += 1;
      this.beginStep();
    },
    pause() {
      clearInterval(this.intervalId);
      this.tick();
      this.running = false;
    },
    resume() {
      this.endAt = Date.now() + this.remaining * 1000;
      this.running = true;
      this.intervalId = setInterval(() => this.tick(), 250);
    },
    adjust(delta) {
      const applied = Math.max(-this.remaining + 1, delta);
      this.stepLength += applied;
      this.remaining += applied;
      this.endAt += applied * 1000;
    },
    skipStep() {
      this.endStep(false);
    },
    skipStretch() {
      const performed = this.currentPerformed;
      clearInterval(this.intervalId);
      if ((this.held[performed] || 0) > 0 || this.current.kind === "hold") {
        // Part of it was actually done — count what was held rather
        // than throwing it away.
        if (this.current.kind === "hold") {
          this.held[performed] = (this.held[performed] || 0) + (this.stepLength - this.remaining);
        }
        this.mark(performed, "done");
      } else {
        this.mark(performed, "skip");
      }
      while (this.current && this.current.performed === performed) this.index += 1;
      this.beginStep();
    },
    mark(performed, action) {
      // The stretch list under the player is its own Alpine component
      // per row; tell the matching row about its new status.
      window.dispatchEvent(
        new CustomEvent("stretch-status", {
          detail: { pk: performed, status: action === "skip" ? "skipped" : "done" },
        }),
      );
      const url = this.$root.dataset.markUrl.replace("/0/", "/" + performed + "/");
      const body = { action };
      if (action === "done") body.actual_seconds = this.held[performed] || 0;
      postJSON(url, body).catch(() => {
        // Offline or a server hiccup — the plain Done/Skip buttons
        // below still let the user record it by hand.
      });
    },
    complete() {
      clearInterval(this.intervalId);
      this.running = false;
      this.finished = true;
      this.remaining = 0;
      this.releaseWakeLock();
      if (this.started) {
        this.audio.beep([784, 988, 1319]);
        this.audio.vibrate([200, 100, 200, 100, 300]);
      }
    },

    // Screen Wake Lock API — keeps the phone from dimming/locking in
    // the middle of a hold while it's lying on the floor next to you.
    // Best-effort: unsupported browsers just behave as before, and the
    // wall-clock countdown still catches up after an unlock.
    requestWakeLock() {
      if (!("wakeLock" in navigator)) return;
      navigator.wakeLock
        .request("screen")
        .then((lock) => {
          this.wakeLock = lock;
        })
        .catch(() => {});
    },
    releaseWakeLock() {
      if (this.wakeLock) {
        this.wakeLock.release().catch(() => {});
        this.wakeLock = null;
      }
    },
  };
}
