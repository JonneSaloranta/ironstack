// One accessible confirmation dialog for every destructive action, replacing
// ~30 native window.confirm() calls. A form opts in with data-confirm="…";
// an HTMX request opts in with hx-confirm="…" (htmx:confirm is intercepted
// below). Built on <dialog>.showModal(), which gives a real focus trap,
// Escape-to-cancel, an inert background, and focus return to the trigger
// for free — the things a hand-rolled .modal-backdrop needs Alpine plugins
// for. Cancel is focused first so a stray Enter never confirms a delete.
(function () {
  var dialog = null;

  function labels() {
    var d = document.body.dataset;
    return {
      ok: d.confirmOk || "Confirm",
      cancel: d.confirmCancel || "Cancel",
    };
  }

  function build() {
    var l = labels();
    dialog = document.createElement("dialog");
    dialog.className = "confirm-dialog";
    dialog.setAttribute("aria-labelledby", "confirm-dialog-message");
    dialog.innerHTML =
      '<form method="dialog">' +
      '<p id="confirm-dialog-message"></p>' +
      '<div class="confirm-dialog-actions">' +
      '<button type="submit" value="cancel" class="button-secondary"></button>' +
      '<button type="submit" value="ok" class="button-danger"></button>' +
      "</div></form>";
    var buttons = dialog.querySelectorAll("button");
    buttons[0].textContent = l.cancel;
    buttons[1].textContent = l.ok;
    document.body.appendChild(dialog);
  }

  function ask(message) {
    if (typeof HTMLDialogElement === "undefined") {
      return Promise.resolve(window.confirm(message));
    }
    if (!dialog) build();
    dialog.querySelector("p").textContent = message;
    return new Promise(function (resolve) {
      dialog.addEventListener(
        "close",
        function () { resolve(dialog.returnValue === "ok"); },
        { once: true }
      );
      dialog.returnValue = "cancel";
      dialog.showModal();
      dialog.querySelector('button[value="cancel"]').focus();
    });
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
    if (form.dataset.confirmed === "1") {
      delete form.dataset.confirmed;
      return;
    }
    event.preventDefault();
    var submitter = event.submitter;
    ask(form.dataset.confirm).then(function (ok) {
      if (!ok) return;
      form.dataset.confirmed = "1";
      if (form.requestSubmit) form.requestSubmit(submitter || undefined);
      else form.submit();
    });
  });

  document.addEventListener("htmx:confirm", function (event) {
    var question = event.detail && event.detail.question;
    if (!question) return;
    event.preventDefault();
    ask(question).then(function (ok) {
      if (ok) event.detail.issueRequest(true);
    });
  });
})();
