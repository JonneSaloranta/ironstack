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
