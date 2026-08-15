// See static/sw.js for exactly what this does and, just as
// deliberately, doesn't do (no offline caching of pages/data).
//
// Extracted out of templates/base.html into its own file so it can be
// loaded via <script src>, not inline — apps.core.middleware.
// ContentSecurityPolicyMiddleware's script-src doesn't allow
// 'unsafe-inline' (only 'unsafe-eval', for Alpine.js's own expression
// evaluation), so an inline <script>...</script> block would simply
// be silently blocked by the browser under that policy. The service
// worker's own URL isn't hardcoded here for the same reason it used
// `{% url 'service-worker' %}` before: read from a data-* attribute
// base.html renders onto <body>, the standard way to pass a
// server-rendered value into an external, template-free script.
if ("serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register(document.body.dataset.serviceWorkerUrl);
  });
}
