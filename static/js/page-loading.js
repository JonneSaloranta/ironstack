/*
 * A thin progress bar along the top of the screen while the app waits on
 * the server — docs/UI_COMPONENTS.md "States". Asked for directly: on a
 * slow connection nothing showed that a tap had registered until the next
 * page finally arrived.
 *
 * Shown for:
 * - a full page load started from the page: following a same-origin link,
 *   or submitting a form normally (also one confirm-dialog.js resubmits);
 * - an HTMX request the user started (a click, a submit, typing in a live
 *   search). Background requests — polling (`every 4s`), `load` triggers —
 *   have no trusted triggering event and are skipped, so a chat thread or
 *   an assistant reply being written doesn't keep the bar flickering.
 *
 * It appears at once, as feedback that the tap registered — a delay
 * before showing it (to spare fast loads a flash) hid it completely on a
 * local network, where pages answer in well under 100 ms. The bar can't
 * know real progress, so it eases towards 90% and finishes when the
 * response arrives. A link with `download` (exports, backups)
 * never unloads the page and is skipped; anything else that unexpectedly
 * doesn't navigate is cleared after GIVE_UP_MS. Purely visual (aria-hidden):
 * a screen reader already announces the new page or the swapped content.
 */
(function () {
  "use strict";

  var GIVE_UP_MS = 20000;

  var bar = null;
  var pending = 0;
  var giveUpTimer = null;

  function ensureBar() {
    if (!bar) {
      bar = document.createElement("div");
      bar.className = "page-loading-bar";
      bar.setAttribute("aria-hidden", "true");
      document.body.appendChild(bar);
    }
    return bar;
  }

  function start() {
    pending += 1;
    if (pending > 1) return;
    clearTimeout(giveUpTimer);
    var el = ensureBar();
    el.classList.remove("is-done");
    // Restart from the beginning even if a previous run is still fading out.
    el.classList.remove("is-loading");
    void el.offsetWidth;
    el.classList.add("is-loading");
    giveUpTimer = setTimeout(reset, GIVE_UP_MS);
  }

  function finish() {
    if (pending === 0) return;
    pending -= 1;
    if (pending > 0) return;
    clearTimeout(giveUpTimer);
    if (bar && bar.classList.contains("is-loading")) {
      bar.classList.add("is-done");
      bar.classList.remove("is-loading");
    }
  }

  function reset() {
    pending = 1;
    finish();
  }

  function isPlainNavigation(event, link) {
    if (event.defaultPrevented || event.button !== 0) return false;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
    if (link.hasAttribute("download")) return false;
    if (link.target && link.target !== "_self") return false;
    // HTMX handles its own links (and reports them through its events).
    if (link.hasAttribute("hx-get") || link.hasAttribute("hx-post") || link.hasAttribute("hx-boost")) {
      return false;
    }
    var url;
    try {
      url = new URL(link.href, window.location.href);
    } catch (e) {
      return false;
    }
    if (url.protocol !== "http:" && url.protocol !== "https:") return false;
    if (url.origin !== window.location.origin) return false;
    // A jump to an anchor on this same page doesn't load anything.
    if (url.pathname === window.location.pathname && url.search === window.location.search && url.hash) {
      return false;
    }
    return true;
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest && event.target.closest("a[href]");
    if (link && isPlainNavigation(event, link)) start();
  });

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (event.defaultPrevented) return;
    if (form.hasAttribute("hx-post") || form.hasAttribute("hx-get") || form.hasAttribute("hx-put")) {
      return;
    }
    if (form.target && form.target !== "_self") return;
    start();
  });

  document.addEventListener("htmx:beforeRequest", function (event) {
    var config = event.detail && event.detail.requestConfig;
    var trigger = config && config.triggeringEvent;
    // A submit is the user's own even when confirm-dialog.js re-issues it
    // (requestSubmit() events aren't "trusted").
    if (!trigger || !(trigger.isTrusted || trigger.type === "submit")) return;
    var xhr = event.detail.xhr;
    if (!xhr) return;
    start();
    // On the request itself, not htmx:afterRequest: that event fires on
    // the triggering element, which the response has often just swapped
    // out of the page (a logged set re-renders its own form), so it never
    // reaches a listener here. loadend covers success, error and abort.
    xhr.addEventListener("loadend", finish, { once: true });
  });

  // Coming back with the browser's back button can restore this page from
  // the back/forward cache with the bar still mid-run.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) reset();
  });
})();
