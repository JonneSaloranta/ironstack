// Keeps keyboard focus when an HTMX swap replaces the element that had
// it. Logging a set swaps the whole exercise card / training panel
// (outerHTML), so the focused submit button was removed and focus fell
// back to <body> - a keyboard or screen-reader user had to find their
// way back from the top of the page after every set.
//
// Only acts when the focused element was inside the swapped target and
// is gone afterwards, so background swaps (a chat poll) never move focus.
// Where it lands, in order: the new content's [data-autofocus] (the next
// set's weight field), an element with the same id as before, the first
// heading, or the swapped container itself.
(function () {
  // Keyed by request: recorded at beforeRequest, because hx-disabled-elt
  // disables the focused submit button right after it, and some browsers
  // drop focus from a disabled element before the response arrives.
  var pending = new WeakMap();

  function targetSelector(elt) {
    return elt && elt.id ? "#" + CSS.escape(elt.id) : null;
  }

  document.addEventListener("htmx:beforeRequest", function (event) {
    var target = event.detail.target;
    var focused = document.activeElement;
    if (!event.detail.xhr || !target || !focused || focused === document.body) return;
    if (!target.contains(focused)) return;
    pending.set(event.detail.xhr, {
      selector: targetSelector(target),
      focusedId: focused.id || null,
    });
  });

  document.addEventListener("htmx:afterSettle", function (event) {
    var info = event.detail.xhr && pending.get(event.detail.xhr);
    if (!info) return;
    pending.delete(event.detail.xhr);
    var focused = document.activeElement;
    if (focused && focused !== document.body && focused.isConnected) return;
    var container = info.selector && document.querySelector(info.selector);
    if (!container) return;
    var target =
      container.querySelector("[data-autofocus]:not([disabled])") ||
      (info.focusedId && document.getElementById(info.focusedId)) ||
      container.querySelector("h1, h2, h3") ||
      container;
    if (target.tabIndex < 0 && !target.matches("a[href], button, input, select, textarea")) {
      target.setAttribute("tabindex", "-1");
    }
    target.focus();
  });
})();
