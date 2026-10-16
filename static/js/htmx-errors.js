// Surfaces failed HTMX requests. Without this a set-log that hit a 500 or a
// dropped connection changed nothing on screen, so the user could believe
// the set had saved. Validation failures (422/400 with a swapped fragment)
// are not handled here — htmx swaps those normally.
(function () {
  function showError(message) {
    var container = document.getElementById("pr-toast-container");
    if (!container) return;
    var banner = document.createElement("div");
    banner.className = "pr-banner pr-banner-error";
    banner.setAttribute("role", "alert");
    banner.textContent = message;
    container.appendChild(banner);
    setTimeout(function () { banner.remove(); }, 10000);
  }
  function text(key, fallback) {
    return (document.body.dataset[key]) || fallback;
  }
  document.addEventListener("htmx:responseError", function () {
    showError(text("htmxErrorServer", "Something went wrong — that change was not saved. Please try again."));
  });
  document.addEventListener("htmx:sendError", function () {
    showError(text("htmxErrorNetwork", "Could not reach the server — check your connection. That change was not saved."));
  });
  document.addEventListener("htmx:timeout", function () {
    showError(text("htmxErrorNetwork", "Could not reach the server — check your connection. That change was not saved."));
  });

  // A composer marked data-reset-on-success (chat, assistant) is
  // cleared only once its send actually went through — not on a
  // network error, a 5xx, or a send the server rejected
  // (X-Form-Rejected), so what the user typed is never thrown away.
  // Any [data-form-error] inside it is emptied on success too.
  document.addEventListener("htmx:afterRequest", function (event) {
    var form = event.detail.elt;
    if (!form || !form.matches || !form.matches("form[data-reset-on-success]")) return;
    var xhr = event.detail.xhr;
    if (!event.detail.successful || (xhr && xhr.getResponseHeader("X-Form-Rejected"))) return;
    form.reset();
    form.querySelectorAll("[data-form-error]").forEach(function (el) { el.innerHTML = ""; });
  });
})();
