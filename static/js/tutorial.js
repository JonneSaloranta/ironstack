/* Guided page tours (apps.tutorials, docs/TUTORIALS.md).
 *
 * Reads the tour templates/base.html renders as JSON (#tutorial-data)
 * and walks through its steps: each step's element — found by its
 * `data-tour="<anchor>"` attribute — is highlighted in its own shape
 * (an outline hugging the element, with its own corner rounding) while
 * the rest of the page is dimmed (but still visible), with a card
 * explaining it placed beside it — below or above, wherever there's
 * room — so the card never sits on top of what it explains. A step whose element isn't on
 * the page (or is hidden) is skipped. Finishing or skipping tells the
 * server (TutorialCompletion) so the tour doesn't start by itself again;
 * "Don't show tutorials" switches automatic tours off altogether.
 *
 * Keyboard: Enter/→ next, ← back, Escape skips; Tab stays inside the
 * card. No dependencies, no Alpine — the same shape as confirm-dialog.js.
 */
(function () {
  "use strict";

  const PADDING = 6; // px between the element and its highlight outline
  const GAP = 12; // px between the highlight, the card and the screen edges
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function start() {
    const dataEl = document.getElementById("tutorial-data");
    if (!dataEl) return;
    const tour = JSON.parse(dataEl.textContent);
    // One overlay at a time: never on top of an open modal/dialog.
    if (document.querySelector(".modal-backdrop:not([style*='display: none']), dialog[open]")) return;

    const steps = tour.steps.filter((step) => !step.anchor || findTarget(step.anchor));
    if (!steps.length) return;

    const previousFocus = document.activeElement;
    const blocker = el("div", "tour-blocker");
    const glass = el("div", "tour-glass");
    const card = el("div", "tour-card");
    card.setAttribute("role", "dialog");
    card.setAttribute("aria-modal", "true");
    card.setAttribute("aria-labelledby", "tour-card-title");
    card.setAttribute("aria-describedby", "tour-card-body");
    card.tabIndex = -1;
    card.innerHTML =
      '<div class="tour-heading">' +
      '<h2 class="card-title" id="tour-card-title"></h2>' +
      '<span class="tour-progress" aria-live="polite"></span>' +
      "</div>" +
      '<p id="tour-card-body"></p>' +
      '<div class="tour-actions">' +
      '<button type="button" class="button-secondary" data-act="back"></button>' +
      '<button type="button" data-act="next"></button>' +
      "</div>" +
      '<div class="tour-secondary">' +
      '<button type="button" class="link-button" data-act="skip"></button>' +
      '<button type="button" class="link-button" data-act="disable"></button>' +
      "</div>";
    card.querySelector('[data-act="skip"]').textContent = tour.labels.skip;
    card.querySelector('[data-act="disable"]').textContent = tour.labels.disable;
    document.body.append(blocker, glass, card);

    let index = 0;
    let target = null;

    function findTarget(anchor) {
      const node = document.querySelector('[data-tour="' + anchor + '"]');
      return node && node.getClientRects().length ? node : null;
    }

    function el(tag, className) {
      const node = document.createElement(tag);
      node.className = className;
      return node;
    }

    // Fixed/sticky chrome (the bottom nav on phones, the top bar on
    // desktop) that the card must not be placed under.
    function insets() {
      const result = { top: 0, bottom: 0 };
      const nav = document.querySelector(".bottom-nav");
      if (!nav || (target && nav.contains(target))) return result;
      const rect = nav.getBoundingClientRect();
      if (rect.height && rect.top < window.innerHeight / 2) result.top = Math.max(0, rect.bottom);
      else if (rect.height) result.bottom = Math.max(0, window.innerHeight - rect.top);
      return result;
    }

    function isPinned(node) {
      for (let n = node; n && n !== document.body; n = n.parentElement) {
        const position = getComputedStyle(n).position;
        if (position === "fixed" || position === "sticky") return true;
      }
      return false;
    }

    // How far to scroll so the element and the card both fit: element
    // near the top with the card under it when there's room, centred
    // otherwise. Clamped to what the page can actually scroll.
    function scrollDelta() {
      if (!target || isPinned(target)) return 0;
      const rect = target.getBoundingClientRect();
      const edge = insets();
      const usable = window.innerHeight - edge.top - edge.bottom;
      const needed = rect.height + PADDING * 2 + GAP * 3 + card.offsetHeight;
      const desiredTop =
        needed <= usable
          ? edge.top + GAP + PADDING
          : edge.top + Math.max(GAP, (usable - rect.height) / 2);
      const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
      const goal = Math.min(Math.max(window.scrollY + rect.top - desiredTop, 0), maxScroll);
      return goal - window.scrollY;
    }

    // The highlight lives in page coordinates (position: absolute), so a
    // scroll carries it along with its element and a step change glides
    // it from one element to the next at the same time as the page
    // scrolls. Only an element pinned to the screen (the bottom nav) gets
    // a screen-fixed highlight.
    function placeGlass() {
      if (!target) {
        glass.hidden = true;
        return;
      }
      const pinned = isPinned(target);
      const rect = target.getBoundingClientRect();
      const offsetX = pinned ? 0 : window.scrollX;
      const offsetY = pinned ? 0 : window.scrollY;
      const radius = parseFloat(getComputedStyle(target).borderTopLeftRadius) || 4;
      glass.hidden = false;
      glass.style.position = pinned ? "fixed" : "absolute";
      glass.style.left = rect.left + offsetX - PADDING + "px";
      glass.style.top = rect.top + offsetY - PADDING + "px";
      glass.style.width = rect.width + PADDING * 2 + "px";
      glass.style.height = rect.height + PADDING * 2 + "px";
      glass.style.borderRadius = radius + PADDING + "px";
    }

    // The card is fixed to the screen, on whichever side of the highlight
    // has room. `pendingScroll` is a scroll that has just been started:
    // the card heads straight for where the element will end up, so card
    // and highlight arrive together.
    function placeCard(pendingScroll) {
      if (!target) {
        card.classList.add("tour-card-center");
        card.style.top = "";
        return;
      }
      card.classList.remove("tour-card-center");
      const rect = target.getBoundingClientRect();
      const shift = isPinned(target) ? 0 : pendingScroll || 0;
      const edge = insets();
      const cardHeight = card.offsetHeight;
      const highlightTop = rect.top - shift - PADDING;
      const highlightBottom = rect.bottom - shift + PADDING;
      const spaceBelow = window.innerHeight - edge.bottom - highlightBottom;
      const spaceAbove = highlightTop - edge.top;
      let top =
        spaceBelow >= cardHeight + GAP * 2 || spaceBelow >= spaceAbove
          ? highlightBottom + GAP
          : highlightTop - GAP - cardHeight;
      const min = edge.top + GAP;
      const max = window.innerHeight - edge.bottom - cardHeight - GAP;
      top = Math.min(Math.max(top, min), Math.max(min, max));
      card.style.top = top + "px";
    }

    function place() {
      placeGlass();
      placeCard(0);
    }

    // The user scrolling by hand: re-seat the card once they stop.
    let scrollTimer = null;
    function onScroll() {
      window.clearTimeout(scrollTimer);
      scrollTimer = window.setTimeout(() => placeCard(0), 150);
    }

    function show(i) {
      index = i;
      const step = steps[index];
      target = step.anchor ? findTarget(step.anchor) : null;
      card.querySelector(".tour-progress").textContent = tour.labels.progress
        .replace("%(current)s", index + 1)
        .replace("%(total)s", steps.length);
      card.querySelector("#tour-card-title").textContent = step.title;
      card.querySelector("#tour-card-body").textContent = step.body;
      const back = card.querySelector('[data-act="back"]');
      back.textContent = tour.labels.back;
      back.hidden = index === 0;
      card.querySelector('[data-act="next"]').textContent =
        index === steps.length - 1 ? tour.labels.done : tour.labels.next;
      const delta = scrollDelta();
      placeGlass();
      placeCard(delta);
      if (delta) {
        window.scrollBy({ top: delta, behavior: reduceMotion ? "auto" : "smooth" });
      }
      card.focus({ preventScroll: true });
    }

    function post(url) {
      return fetch(url, {
        method: "POST",
        headers: { "X-CSRFToken": tour.csrfToken },
        credentials: "same-origin",
      }).catch(() => {});
    }

    function finish(disable) {
      post(tour.completeUrl);
      if (disable) post(tour.disableUrl);
      blocker.remove();
      glass.remove();
      card.remove();
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", onScroll, true);
      window.clearTimeout(scrollTimer);
      document.removeEventListener("keydown", onKey, true);
      if (previousFocus && previousFocus.focus) previousFocus.focus();
    }

    function onKey(event) {
      if (event.key === "Escape") {
        event.preventDefault();
        finish(false);
      } else if (event.key === "ArrowRight" && index < steps.length - 1) {
        show(index + 1);
      } else if (event.key === "ArrowLeft" && index > 0) {
        show(index - 1);
      } else if (event.key === "Tab") {
        const buttons = [...card.querySelectorAll("button:not([hidden])")];
        const first = buttons[0];
        const last = buttons[buttons.length - 1];
        if (event.shiftKey && (document.activeElement === first || document.activeElement === card)) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    }

    card.addEventListener("click", (event) => {
      const act = event.target.closest("[data-act]")?.dataset.act;
      if (act === "next") {
        if (index < steps.length - 1) show(index + 1);
        else finish(false);
      } else if (act === "back") {
        show(index - 1);
      } else if (act === "skip") {
        finish(false);
      } else if (act === "disable") {
        finish(true);
      }
    });
    window.addEventListener("resize", place);
    window.addEventListener("scroll", onScroll, true);
    document.addEventListener("keydown", onKey, true);
    show(0);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }
})();
