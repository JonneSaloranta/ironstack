// Achievements carousel (apps.analytics.achievements) — auto-advances
// through whichever highlight cards the server rendered, pausing on
// hover/focus so a user reading one isn't fighting the timer, and the
// dot row (also Alpine-driven) always lets a user jump straight to a
// specific card instead — "auto-rotate" is only ever the default.
//
// Extracted out of templates/core/dashboard.html into its own file so
// it can be loaded via <script src>, not inline — see
// static/js/sw-register.js's own comment for why (CSP's script-src
// here has no 'unsafe-inline'). `total` still comes from Alpine's own
// `x-data="ironstackCarousel({{ achievements|length }})"` expression
// in the template itself, evaluated via Alpine's ('unsafe-eval'-gated)
// Function()-based interpreter, not from anything templated into this
// file — this file itself contains no server-rendered value.
//
// Moving content needs a way to stop it (WCAG 2.2.2): the Pause button
// stops rotation for good, hover and keyboard focus pause it while
// they last, and with prefers-reduced-motion it never starts on its own.
function ironstackCarousel(total) {
  return {
    index: 0,
    total: total,
    timer: null,
    paused: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
    start() {
      clearInterval(this.timer);
      if (this.total < 2 || this.paused) return;
      this.timer = setInterval(() => {
        this.index = (this.index + 1) % this.total;
      }, 4500);
    },
    stop() {
      clearInterval(this.timer);
    },
    togglePaused() {
      this.paused = !this.paused;
      this.paused ? this.stop() : this.start();
    },
    goto(i) {
      this.index = i;
      this.start();
    },
  };
}
