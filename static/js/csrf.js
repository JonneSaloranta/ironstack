// Shared by every page that POSTs JSON via fetch() instead of a real
// <form> with {% csrf_token %} — static/js/push-subscribe.js and
// static/js/rest-timer.js both need this exact pair, so it lives here
// once instead of each carrying its own copy (CLAUDE.md — don't
// duplicate an abstraction that can be extended/shared instead).
// Loaded via a plain <script defer> tag, same as every other file in
// this codebase (no bundler), so these are just ordinary globals —
// any page including this file before the one that needs it can call
// getCsrfCookie()/postJSON() directly.
function getCsrfCookie() {
  const match = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : null;
}

function postJSON(url, body) {
  return fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-CSRFToken": getCsrfCookie() },
    body: JSON.stringify(body),
  });
}
