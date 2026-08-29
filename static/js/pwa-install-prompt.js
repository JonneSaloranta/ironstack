// Recommends installing IronStack to the home screen — never forces
// it. Three ways to back off, all persisted to localStorage rather
// than a server-side Profile field, same reasoning as static/js/rest-
// timer.js's own `muted` flag: installability, and a user's choice
// about it, is a device/browser thing, not an account-wide setting:
//   - the × closes the banner for this visit and delays it again
//     for REMIND_LATER_DAYS, same as the explicit "Remind me later"
//     button — pressing either means "not now", not "never".
//   - "Don't remind me" opts out permanently.
//
// Two very different install paths depending on the browser:
//   - Chrome/Edge/most Android browsers fire `beforeinstallprompt`
//     ahead of time; calling preventDefault() on it lets *this*
//     banner control when the prompt appears instead of the browser's
//     own mini-infobar, and install() below replays it on demand via
//     .prompt() — this is "the browser's own install notification"
//     the product ask wants used whenever it's actually available.
//   - iOS Safari never fires that event — there is no programmatic
//     install API there at all, "Add to Home Screen" only exists
//     behind the Share sheet a user has to open by hand — so the best
//     this can do is show instructions for the same result. Any other
//     browser with neither (desktop Firefox, Chrome/Firefox on iOS,
//     ...) gets no prompt at all rather than a guess that might point
//     at a menu that browser doesn't have.
const DISMISS_UNTIL_KEY = "ironstack-pwa-install-dismissed-until";
const DISMISS_FOREVER_KEY = "ironstack-pwa-install-dismissed-forever";
const REMIND_LATER_DAYS = 14;

// Captured at the top level, not inside the Alpine component below,
// since `beforeinstallprompt` can fire before Alpine has even
// initialized this component — this file's <script> tag loads before
// alpine.min.js's own in templates/base.html for that same "must
// exist before Alpine looks for it" reason (see that file's comment
// on script ordering).
let deferredInstallPrompt = null;
window.addEventListener("beforeinstallprompt", (event) => {
  event.preventDefault();
  deferredInstallPrompt = event;
  document.dispatchEvent(new CustomEvent("ironstack:install-prompt-ready"));
});

function isStandalone() {
  return (
    window.matchMedia("(display-mode: standalone)").matches ||
    window.navigator.standalone === true // iOS Safari's own flag once added to the home screen
  );
}

function isIOSSafari() {
  const ua = window.navigator.userAgent;
  const isIOS = /iphone|ipad|ipod/i.test(ua) && !window.MSStream;
  // Every iOS browser is WebKit under the hood, but only Safari itself
  // exposes the Share-sheet "Add to Home Screen" action these
  // instructions point at — Chrome/Firefox/Edge on iOS carry their own
  // UA tokens (CriOS/FxiOS/EdgiOS) alongside "Safari", so excluding
  // those avoids sending their users looking for a button that isn't
  // actually there in their browser.
  const isSafari = /safari/i.test(ua) && !/crios|fxios|edgios/i.test(ua);
  return isIOS && isSafari;
}

function ironstackPwaInstallPrompt() {
  return {
    visible: false,
    mode: null, // "native" | "ios-instructions"

    init() {
      if (isStandalone()) return; // already installed — nothing to recommend
      if (localStorage.getItem(DISMISS_FOREVER_KEY) === "true") return;
      const dismissedUntil = Number(localStorage.getItem(DISMISS_UNTIL_KEY) || 0);
      if (Date.now() < dismissedUntil) return;

      if (deferredInstallPrompt) {
        this.showNative();
      } else {
        // beforeinstallprompt may simply not have fired *yet* even on
        // a browser that supports it — its timing is up to that
        // browser's own engagement heuristics, not this page load —
        // so keep listening rather than only checking the
        // already-captured value once here.
        document.addEventListener("ironstack:install-prompt-ready", () => this.showNative(), {
          once: true,
        });
        if (isIOSSafari()) {
          this.mode = "ios-instructions";
          this.visible = true;
        }
      }
    },

    showNative() {
      this.mode = "native";
      this.visible = true;
    },

    async install() {
      if (!deferredInstallPrompt) return;
      deferredInstallPrompt.prompt();
      await deferredInstallPrompt.userChoice;
      // A `beforeinstallprompt` event can only be `.prompt()`ed once —
      // whatever the user chose in the native dialog, there is nothing
      // left for this banner to offer for the rest of this page load.
      deferredInstallPrompt = null;
      this.visible = false;
    },

    remindLater() {
      const until = Date.now() + REMIND_LATER_DAYS * 24 * 60 * 60 * 1000;
      localStorage.setItem(DISMISS_UNTIL_KEY, String(until));
      this.visible = false;
    },

    dismissForever() {
      localStorage.setItem(DISMISS_FOREVER_KEY, "true");
      this.visible = false;
    },
  };
}
