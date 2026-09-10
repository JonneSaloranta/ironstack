# Vendored axe-core

`axe.min.js` — [axe-core](https://github.com/dequelabs/axe-core) v4.10.2,
downloaded unmodified from
`https://cdn.jsdelivr.net/npm/axe-core@4.10.2/axe.min.js` (MPL-2.0), used
by `apps/core/test_accessibility.py`.

Vendored rather than loaded from a CDN at test time for two reasons:

- This app's own Content-Security-Policy (`apps.core.middleware.
  ContentSecurityPolicyMiddleware`, `docs/SECURITY.md`
  "Content-Security-Policy") has no external `script-src` allowance —
  loading a `<script src="https://...">` from a real page under test
  is blocked by the exact same policy a real visitor's browser
  enforces, the same reason `drf-spectacular-sidecar` vendors Swagger
  UI's own JS/CSS instead of using its CDN-hosted default (see
  `requirements/base.txt`'s own comment on that package). The test
  file injects this script's contents directly via Playwright's
  `page.evaluate()` instead, which runs through the DevTools protocol
  rather than a page-level `<script>` tag, so CSP never sees it.
- Pinned to an exact version on purpose, like every other dependency
  in this project (`requirements/*.txt`, `Dockerfile`'s `FROM` tags,
  ...) — a moving "latest axe-core" would silently change which WCAG
  rules run, and which pages pass or fail, on whatever day CI happens
  to run.

To upgrade: download the new version's `axe.min.js` from
`https://cdn.jsdelivr.net/npm/axe-core@<version>/axe.min.js`, replace
this file, and bump the version in `apps/core/test_accessibility.py`'s
own `AXE_CORE_VERSION` (kept there, not duplicated here, so there's one
place recording which version is actually in use) — after actually
reading what changed between versions, the same rule
`.github/dependabot.yml`'s own comment already applies to every other
pinned dependency in this project.
