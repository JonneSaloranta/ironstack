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
- **Put a TLS-terminating reverse proxy in front of this stack**
  (Caddy, Traefik, a cloud load balancer, a Cloudflare/Tailscale tunnel,
  ...) that forwards `X-Forwarded-Proto: https` for real HTTPS
  requests. This is the recommended path — it keeps certificate
  management out of this repo entirely.
- **Add TLS directly to `compose/nginx/nginx.conf`** (a `listen 443
  ssl` server block with your own certificate, e.g. via a
  `certbot`/`acme.sh` sidecar you manage yourself) if you'd rather this
  stack be the TLS endpoint.
- **Explicitly set `DJANGO_SECURE_SSL_REDIRECT=false`** in `.env` if
  you're intentionally running HTTP-only (e.g. behind a trusted
  internal network with no public exposure). Understand this means
  traffic between the client and this stack is unencrypted.

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
