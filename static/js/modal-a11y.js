// Focus management for the app's Alpine/HTMX-driven modals (.modal-backdrop
// wrapping a role="dialog" .modal-card). They open by toggling x-show or
// by an HTMX swap, so none of them moved focus, trapped Tab, or gave focus
// back on close. This adds those three behaviours without rewriting each
// modal: it watches for a backdrop becoming visible / hidden / removed.
(function () {
  var active = []; // [{el, opener}] — stack, last is topmost
  var FOCUSABLE =
    'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), ' +
    'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

  function isVisible(el) {
    return el.isConnected && getComputedStyle(el).display !== "none";
  }

  function focusables(root) {
    return Array.prototype.filter.call(root.querySelectorAll(FOCUSABLE), function (n) {
      return n.offsetParent !== null || n === document.activeElement;
    });
  }

  function activate(el) {
    active.push({ el: el, opener: document.activeElement });
    var card = el.querySelector(".modal-card") || el;
    requestAnimationFrame(function () {
      var body = card.querySelector(".modal-body");
      var target = (body && focusables(body)[0]) || focusables(card)[0];
      if (!target) {
        card.setAttribute("tabindex", "-1");
        target = card;
      }
      target.focus();
    });
  }

  function deactivate(entry) {
    active.splice(active.indexOf(entry), 1);
    var opener = entry.opener;
    if (opener && opener.isConnected && typeof opener.focus === "function") {
      opener.focus();
    }
  }

  function sync() {
    active.slice().forEach(function (entry) {
      if (!isVisible(entry.el)) deactivate(entry);
    });
    document.querySelectorAll(".modal-backdrop").forEach(function (el) {
      if (isVisible(el) && !active.some(function (e) { return e.el === el; })) {
        activate(el);
      }
    });
  }

  new MutationObserver(sync).observe(document.documentElement, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ["style", "class", "hidden"],
  });

  document.addEventListener("keydown", function (event) {
    var top = active[active.length - 1];
    if (!top) return;
    if (event.key === "Escape" && !top.el.hasAttribute("x-show")) {
      // Server-rendered modal (onboarding) has no Alpine Escape handler:
      // treat Escape as its dismiss (×) button.
      var close = top.el.querySelector(".modal-close");
      if (close) {
        event.preventDefault();
        close.click();
      }
      return;
    }
    if (event.key !== "Tab") return;
    var items = focusables(top.el.querySelector(".modal-card") || top.el);
    if (!items.length) {
      event.preventDefault();
      return;
    }
    var first = items[0];
    var last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  });

  document.addEventListener("DOMContentLoaded", sync);
})();
