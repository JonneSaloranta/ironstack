# Security Requirements

## Authentication

Use Django authentication and a custom user model.

`apps.api` (see `docs/API.md`) adds a second, deliberately separate
authentication path — per-user API keys (`Authorization: Bearer`), never
session/cookie auth — for programmatic access. A key's secret is shown
once at creation and stored only as a SHA-256 hash (`ApiKey.key_hash`);
authorization is checked per-request against that specific key's own
per-context CRUD permissions (`apps.api.permissions.HasContextPermission`),
never against the wider Django permission system.

Production requirements:
- DEBUG=False
- secure cookies
- CSRF protection
- security middleware
- appropriate ALLOWED_HOSTS
- HTTPS behind the reverse proxy

### TLS: read this before your first production deploy

`config.settings.production` defaults `DJANGO_SECURE_SSL_REDIRECT` to
`true` and trusts `X-Forwarded-Proto` from the reverse proxy
(`SECURE_PROXY_SSL_HEADER`) — but the bundled `compose/nginx/nginx.conf`
only listens on plain port 80 and does **not** terminate TLS itself (no
certificate management is bundled — there's no domain name to
provision one for generically). Deployed exactly as shipped, this
combination is a broken deployment: nginx always reports
`X-Forwarded-Proto: http`, so Django redirects every request to HTTPS,
which routes right back through the same HTTP-only nginx — an infinite
redirect loop.

Before deploying, do one of:
- **Use `docker-compose.tls.yml`** — a ready-to-use overlay that
  replaces the bundled nginx with Caddy, which provisions and renews a
  real Let's Encrypt certificate on its own given just a domain name
  already pointed at this host:
  `IRONSTACK_DOMAIN=your.domain.example docker compose -f
  docker-compose.yml -f docker-compose.tls.yml up -d`. This is the
  same recommendation as the next bullet, just already wired up.
- **Put a TLS-terminating reverse proxy in front of this stack**
  (Traefik, a cloud load balancer, a Cloudflare/Tailscale tunnel, ...)
  that forwards `X-Forwarded-Proto: https` for real HTTPS requests, if
  you'd rather use something other than the bundled Caddy option.
- **Add TLS directly to `compose/nginx/nginx.conf`** (a `listen 443
  ssl` server block with your own certificate, e.g. via a
  `certbot`/`acme.sh` sidecar you manage yourself) if you'd rather the
  bundled nginx stay the TLS endpoint.
- **Explicitly set `DJANGO_SECURE_SSL_REDIRECT=false`** in `.env` if
  you're intentionally running HTTP-only (e.g. behind a trusted
  internal network with no public exposure). Understand this means
  traffic between the client and this stack is unencrypted.

## Email

Password reset (`django.contrib.auth`'s own views, wired in
`config.urls`) needs a working `EMAIL_BACKEND` to actually deliver
anywhere. Set `DJANGO_EMAIL_HOST` (plus `DJANGO_EMAIL_PORT`/
`_USER`/`_PASSWORD`/`_USE_TLS`, `.env.example`) to use real SMTP;
leave it unset and outgoing mail is just logged to the console instead
(`docker compose logs web`) — password reset still "completes"
without erroring, it just never reaches anyone.

Set `DJANGO_ADMINS` (`"Name:email,Name2:email2"`) to also get emailed
— through that same backend — on every uncaught server exception, once
`DEBUG=False`. This is Django's own built-in `mail_admins` error
reporting (`django.utils.log.AdminEmailHandler`, wired by Django's
default `LOGGING` config with no override needed here) — deliberately
not a dependency like Sentry: this instance either already needs SMTP
configured for password reset, in which case error emails are free, or
it doesn't, in which case the honest fallback is still "check `docker
compose logs`", the same as before this existed.

## Brute-force protection

`django.contrib.auth`'s bare login view has no rate limiting at all —
`apps.api`'s per-key rate-limit tiers are a completely separate,
API-key-only mechanism that never applies to the session-based web
login. `apps.accounts.forms.RateLimitedAuthenticationForm` (wired in
via `apps.accounts.views.RateLimitedLoginView`, registered ahead of
`django.contrib.auth.urls`' own `login/` in `config.urls`) blocks
further attempts from the same client IP for 15 minutes after 5 failed
attempts within that window, using the same shared `DatabaseCache`
`apps.api`'s throttling does. Keyed by client IP (`X-Real-IP`, which
both `compose/nginx/nginx.conf` and `compose/caddy/Caddyfile` set —
see `apps.accounts.forms._client_ip`'s own docstring for why plain
`REMOTE_ADDR` doesn't work behind either proxy), not the submitted
username, so an attacker can't lock a real user out on purpose by
deliberately failing their login from elsewhere.

The password-reset request view (`django.contrib.auth`'s
`PasswordResetView`) had the identical gap — nothing stopped it being
used as a free tool to spam an arbitrary email address with reset
links over and over, using this instance's own SMTP relay to do it.
`apps.accounts.forms.RateLimitedPasswordResetForm` (wired in via
`apps.accounts.views.RateLimitedPasswordResetView`, registered ahead
of `django.contrib.auth.urls`' own `password_reset/` the same way the
login override is) applies the same 5-per-15-minutes, per-client-IP
limit — every submission counts here, not just ones for a real email
address, since there's no such thing as a "failed" password-reset
request to count selectively.

## Cross-Site Request Forgery (CSRF)

`config.settings.production` derives `CSRF_TRUSTED_ORIGINS` from
`DJANGO_ALLOWED_HOSTS` automatically — every host this instance
answers to is also a host a real POST request to it should be trusted
to have come from. If you see "CSRF verification failed" in production
after changing how this instance is reached (a new domain, a proxy
added in front, `DJANGO_ALLOWED_HOSTS` not updated to match), that
mismatch is the first thing to check — Django 4+'s CSRF check compares
the request's `Origin`/`Referer` against this list for HTTPS requests.

## Content-Security-Policy

`apps.core.middleware.ContentSecurityPolicyMiddleware` sets a CSP
header on every response, in every environment. `default-src 'self'`
plus tight per-directive allowances (no external scripts/styles/
fonts/images beyond `data:` URIs, no framing by another site, no
plugins, forms can only submit back to this same origin) — see the
middleware's own docstring for the exact policy string and reasoning.

Two allowances are worth knowing about, both scoped as narrowly as
this stack currently allows:
- `script-src 'unsafe-eval'` — Alpine.js evaluates `x-data`/`x-show`/
  `@click`/... expression strings via `new Function()`, which CSP
  treats as eval. Alpine ships a separate CSP-safe build (a restricted
  expression parser) that would let this be dropped; not adopted here.
- `style-src 'unsafe-inline'` — a number of templates use plain
  `style="..."` attributes rather than a dedicated class.

Every template's own inline `<script>` block and every native
`onclick=`/`onsubmit=` attribute were removed as part of adding this
header (moved to `static/js/*.js` loaded via `<script src>`, or
converted to Alpine's own `x-data`/`@submit`/`@click` directive syntax
— which is not a native browser inline-script mechanism at all, so it
isn't affected by `script-src` lacking `'unsafe-inline'`). Don't
reintroduce either in a new template — either would simply be silently
blocked by the browser under this policy, with no server-side error to
notice it by.

## Dependency updates

`.github/dependabot.yml` opens a weekly, reviewed PR for outdated pip
(`requirements/`), Docker base image, and GitHub Actions dependencies
— plain GitHub configuration, not a new runtime dependency. Every PR
still has to pass `.github/workflows/ci.yml` (ruff, migration check,
the full test suite) before merging like any other change.
`requirements/*.txt` are range-pinned (`>=X,<Y`) rather than hash-
locked, so most weeks this picks up patch releases within that range.

## Registration

Self-hosted doesn't necessarily mean "open to the public internet" —
`DJANGO_SIGNUP_ENABLED=false` closes self-service registration
(`apps.accounts.views.SignupView`) once your intended users all have
accounts, or from the start if this instance is reachable beyond a
trusted network. Gates the URL itself, not just the login page's link
to it. Existing users can still log in either way.

## Authorization

Every user-owned query must be scoped to the authenticated user.

Never trust an object ID supplied by the client without checking ownership.

Test cross-user access explicitly.

## Sensitive configuration

Never commit:
- SECRET_KEY
- database passwords
- production credentials
- API tokens

Use environment variables or deployment secrets.

## Data isolation

Users must not be able to access another user's:
- programs
- workout sessions
- exercise sets
- measurements
- activities
- analytics

## Auditability

For important destructive or irreversible operations, consider preserving historical records instead of deleting them.

Workout history should be treated as durable data.
