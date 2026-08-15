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
function ironstackCarousel(total) {
  return {
    index: 0,
    total: total,
    timer: null,
    start() {
      if (this.total < 2) return;
      clearInterval(this.timer);
      this.timer = setInterval(() => {
        this.index = (this.index + 1) % this.total;
      }, 4500);
    },
    stop() {
      clearInterval(this.timer);
    },
    goto(i) {
      this.index = i;
      this.start();
    },
  };
}
