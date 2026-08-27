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

## Two-factor authentication

Optional, per-user, TOTP-based (RFC 6238) — Profile → "Two-factor
authentication" → "Set up", scanning the QR code into any standard
authenticator app. Implemented with `pyotp` (secret generation/
verification) and `qrcode[pil]` (rendering the setup QR as an inline
`data:image/png;base64,...` image, no separate image-serving endpoint)
rather than `django-otp`: TOTP's crypto is security-critical and
shouldn't be hand-rolled, but this app only ever needed exactly the
RFC 6238 generate/verify pair for a single authenticator per user, not
`django-otp`'s heavier multi-device/multi-method framework.

**The TOTP secret (`User.totp_secret`) is stored as plain text, not
encrypted at rest.** This is a deliberate trade-off, not an oversight:
unlike a password, the server has to be able to read the secret back
on every login to compute the expected 6-digit code itself — a
one-way hash (as used for passwords) can't work here. Doing this
properly would mean field-level encryption with its own separately-
managed key, which this project has no existing infrastructure for.
Anyone with read access to the production database (or a downloaded
backup, see `docs/BACKUP.md`) can therefore reconstruct a user's live
TOTP codes. Treat database access and backup files with the same care
as you would the passwords table.

Backup codes (`TwoFactorBackupCode`, 10 generated at setup and on any
regeneration) are hashed with Django's own password hasher
(`make_password`/`check_password`), not a fast digest like
`apps.api.models.ApiKey.key_hash`'s SHA-256 — a backup code is
entered as rarely as a password and deserves the same slow-hash
treatment, unlike an API key that's checked on every request where a
fast hash matters for server load. Each code is single-use
(`used_at` is stamped the moment one is consumed).

That same deliberately-slow hasher makes generating a fresh set of 10
codes take several real seconds on ordinary hardware — reported live
as looking like nothing was happening at all, which led to a user
re-submitting mid-wait and racing themselves out of ever seeing the
codes their first click had already generated. Fixed by moving the
actual generation off the request that first lands a user on the
result (`apps.accounts.views.TwoFactorBackupCodesView`/
`TwoFactorBackupCodesFragmentView`): the page renders instantly with a
visible spinner, then loads the (slow) result via one HTMX request
with nothing left to click a second time.

The login flow's second step
(`apps.accounts.views.TwoFactorVerifyView`, reached only via
`RateLimitedLoginView.form_valid`'s redirect once the password alone
was already correct) has its own rate limit, separate from the
brute-force protection above: 5 incorrect codes within 5 minutes locks
out further attempts, **keyed by user ID rather than client IP**. This
is deliberate: by this step an attacker already has a correct
password and a specific account in mind, so an IP-keyed limit alone
would be trivially routed around by retrying from a different address.
Setup's own confirmation step (before `totp_enabled` ever flips to
`True`) has no rate limit — nothing sensitive is guarded yet at that
point, and the secret used there is discarded/regenerated on any
retry if the user abandons setup.

**Recovery for a fully locked-out user** (lost authenticator device
*and* lost/exhausted backup codes) has no self-service path by
design — that would defeat the point of a second factor. An
administrator can clear it from Django admin: the `User` list has a
"Disable two-factor authentication for selected users" action
(`apps.accounts.admin.UserAdmin.disable_two_factor`), which clears
`totp_enabled`, `totp_secret`, and every backup code for the selected
users, letting them log in with just their password again and,
if they choose, set 2FA back up from scratch.

## Single sign-on (Authentik / OIDC)

Optional. This app can act as an OpenID Connect Relying Party against
an externally-run [Authentik](https://goauthentik.io/) instance, via
`mozilla-django-oidc` (`apps.accounts.oidc`). Nothing about it
activates unless `AUTHENTIK_URL`, `AUTHENTIK_CLIENT_ID`, and
`AUTHENTIK_CLIENT_SECRET` are all set (`settings.AUTHENTIK_ENABLED`,
`.env.example`) — a self-hosted instance that doesn't run Authentik
pays no cost beyond the dependency itself: no new URLs, no "Log in
with Authentik" button, `AUTHENTICATION_BACKENDS` stays
`ModelBackend`-only.

**Setup, on the Authentik side**: create an "OAuth2/OpenID Provider"
(Applications → Providers), redirect URI
`<this app's own base URL>/oidc/callback/`, then an Application using
that provider. `AUTHENTIK_CLIENT_ID`/`AUTHENTIK_CLIENT_SECRET` come
from the created provider; `AUTHENTIK_APPLICATION_SLUG` must match the
Application's own slug (Authentik's `issuer_mode="per_provider"`
default mints a distinct OIDC issuer/JWKS per application, not one
shared endpoint).

**Alongside local login, not instead of it, by default.** The login
page shows both a "Log in with Authentik" button and the existing
username/password form. `DJANGO_PASSWORD_LOGIN_ENABLED=false`
(`settings.PASSWORD_LOGIN_ENABLED`) closes the local form entirely —
gated directly in `RateLimitedLoginView`/`SignupView`/
`RateLimitedPasswordResetView.dispatch`, the same "block the URL
itself, not just hide the link" approach `DJANGO_SIGNUP_ENABLED`
already uses, not merely hidden in the template. `/admin/` is
deliberately never affected by this flag — it's Django's own separate
login view (`apps.core.admin`), kept as a break-glass path so a
misconfigured or unreachable Authentik instance can never lock every
admin out of this app at once.

**Account matching and provisioning**
(`apps.accounts.oidc.IronStackOIDCAuthenticationBackend`): a user
authenticating via Authentik is matched to an existing local account
by email (`mozilla_django_oidc`'s own default
`filter_users_by_claims`) — so a local-password account and its
owner's Authentik identity link up automatically the first time they
use "Log in with Authentik", no separate linking step. If no account
matches, one is created automatically (username from Authentik's
`preferred_username` claim, deduplicated with a numeric suffix on
collision; email and display name from its `email`/`name` claims; an
unusable local password, same as `User.set_unusable_password()` —
Django's own `create_user(password=None)` default). Either way, that
account's `User.is_sso_user` is set `True` — purely informational (a
note on the profile page), never something that gates access on its
own.

**If Authentik runs as a separate Docker stack**: `AUTHENTIK_URL` is
what the user's *browser* is redirected to, but this app's own
container makes its own direct server-to-server calls too (token
exchange, JWKS fetch, userinfo fetch) — and from inside a container,
`localhost`/a published host port usually doesn't reach a sibling
Docker stack the way it does from the host itself. `AUTHENTIK_INTERNAL_URL`
(`.env.example`) overrides just those server-side calls to a
different, container-reachable address (e.g. Authentik's container
hostname on a Docker network this app's container has also been
attached to — `docker network connect`), while `AUTHENTIK_URL` keeps
driving what the browser is actually sent to. Defaults to
`AUTHENTIK_URL`; most single-host or non-containerized-Authentik
deployments never need to set it.

**Restricting who can actually use it**: having *any* account on the
Authentik instance is enough to complete "Log in with Authentik" by
default — a real concern once that Authentik instance also
authenticates other, unrelated applications (self-hosters commonly run
one shared Authentik for everything). Two independent layers, meant to
be used together:
- **On the Authentik side** (the primary control): Applications → the
  IronStack application → Policy / Group / User Bindings → bind the
  Application to a specific group. A user outside that group is
  rejected by Authentik itself, before this app's callback ever runs.
- **`AUTHENTIK_REQUIRED_GROUP`** (`settings.AUTHENTIK_REQUIRED_GROUP`,
  `apps.accounts.oidc.IronStackOIDCAuthenticationBackend.verify_claims`):
  IronStack's own check against the `groups` claim Authentik's default
  OpenID `profile` scope mapping sends. Defense in depth, not a
  substitute for the Authentik-side binding above — it still trusts
  whatever `groups` the verified id_token/userinfo response says, so
  it only helps if that claim is actually accurate, but it means
  access doesn't rest solely on the Authentik-side policy being
  configured correctly, or at all. Left unset (the default), any
  Authentik account can log in, same as before this setting existed.

**2FA note**: an SSO login goes straight from Authentik's own callback
to `django.contrib.auth.login()` — it never touches
`RateLimitedLoginView`/`TwoFactorVerifyView` at all, so this app's own
TOTP second factor (above) is bypassed for that login, deliberately:
Authentik is expected to enforce its own MFA policy upstream instead.
An account with `totp_enabled=True` that also logs in via Authentik
still has that TOTP requirement enforced on any *local password*
login it might still be able to do.

## Gravatar profile picture

`apps.accounts.models.User.show_gravatar` (profile page → preferences)
shows a user's Gravatar (https://gravatar.com) picture at the top of
their own profile page, if the email on their account has one. Off by
default, unlike this app's other display/privacy toggles
(`show_bmi`/`show_achievements`/`show_name_to_others`): every one of
those only changes what this instance itself shows, entirely within
its own infrastructure. This one is the only place in the whole app
where a user's browser talks to a server this instance doesn't run —
turning it on means the browser fetches `User.gravatar_url()` directly
from `gravatar.com` on every profile page load, handing Gravatar (a
service run by Automattic) this user's IP address and a SHA-256 hash
of their email. That's a deliberate, narrow exception to CLAUDE.md's
"runs entirely on the user's own infrastructure" — the user has to opt
into it explicitly, nothing fetches or displays it on their behalf
until they do.

`gravatar_url()` requests Gravatar's `d=404` fallback behavior (a 404
response instead of a generated placeholder image) so the template can
tell "no Gravatar for this email" apart from "Gravatar is
unreachable" — either way, the `<img>` just disappears
(`templates/accounts/profile.html`'s `@error="$el.remove()"`, an
Alpine directive rather than a native `onerror=` attribute, since the
latter would be silently blocked by this app's own CSP script-src —
see below) rather than showing a broken-image icon or Gravatar's own
default avatar for someone who never actually created a Gravatar
account.

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
fonts/images beyond `data:` URIs and `gravatar.com`, no framing by
another site, no plugins, forms can only submit back to this same
origin) — see the middleware's own docstring for the exact policy
string and reasoning.

Three allowances are worth knowing about, each scoped as narrowly as
this stack currently allows:
- `img-src https://www.gravatar.com` — the one deliberate exception to
  "no external anything," letting `templates/accounts/profile.html`
  load a user's Gravatar picture when they've opted into
  `User.show_gravatar` (off by default). See "Gravatar profile
  picture" above.
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

## Search engine indexing

Off by default — `apps.core.models.SeoSettings` (Profile →
Administration → Site & SEO), a single admin-tunable boolean matching
the same singleton pattern as `BackupSettings`/`FeedbackSettings`.
Most installs of this app are a private, self-hosted instance running
for one household and holding another person's health data, not a
public site anyone should be finding through a search engine — the
safe default is a search engine never indexing it at all, not an
operator having to remember to opt out on a fresh install. Two
independent, redundant signals carry this, since a crawler only
respects whichever one it actually checks: `/robots.txt`
(`apps.core.views.robots_txt`, generated per-request, `Disallow: /`
when off) and a `<meta name="robots" content="noindex, nofollow">` tag
on every single page (`templates/base.html`,
`apps.core.context_processors.seo`). Turning the setting on flips both
to `Allow: /`/`index, follow` — meaningful only for an operator who
deliberately wants their own instance publicly discoverable.

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

## Account deletion (GDPR)

Profile → "Delete account" (`apps.accounts.views.AccountDeleteView`,
`apps.accounts.services.delete_account`) — self-service exercise of
GDPR Article 17 ("right to erasure"), the one deliberate exception to
"prefer preserving historical records" below: erasure only means
something if it's actually irreversible, so this is a genuine hard
delete of everything exclusively personal to that account, not a
soft-delete flag or an anonymized retained copy.

Two different treatments, not one blanket "delete everything the user
touched":

- **Exclusively personal data is hard-deleted.** Every model that can
  only ever mean "this specific person's own record" (workout
  history, personal records, body measurements, the nutrition diary/
  targets/diet plans, activities, feedback, API keys) already has
  `on_delete=models.CASCADE` on its FK to `User` — `delete_account`
  doesn't need to know any of their names, `user.delete()` cascades
  through all of it in one transaction.
- **Shared reference content this user happened to create is
  reassigned, not deleted.** A custom Exercise/Food/Recipe/Program/
  MealSlot/ActivityType/MeasurementType has `owner` set to this user,
  but this app's whole shared-instance design means another user may
  already be actively using it — a workout logged against a custom
  exercise, a diary entry against a custom food. `delete_account`
  reassigns `owner` to `None` (the same "shared, built-in default"
  meaning `owner=None` already has everywhere in this app) *before*
  deleting the user, rather than delete these outright: Exercise's
  own usage FK (`apps.workouts.models.PerformedExercise.exercise`) is
  deliberately `on_delete=models.PROTECT`, so a still-referenced one
  can never vanish out from under someone still using it, and
  reassigning first is what lets the CASCADE above proceed without
  ever touching these rows or erroring out.

An account with no local password (`is_sso_user`, `docs/SECURITY.md`
"Single sign-on") has nothing to re-enter as a password, so it types
its own username to confirm instead — the same "prove you meant this"
strength for an action that can't ask for a password that doesn't
exist. The instance's last remaining superuser is blocked from
self-deleting (a clear error, not a silent no-op) — promoting another
account first is required, so the instance can never end up with no
one able to reach Django admin at all.

**What this can't reach: existing backup archives**
(`docs/BACKUP.md`). A backup is a point-in-time snapshot; one made
before an account deletion still contains that account's data until
it's rotated out by `BackupSettings.retention_count`, the same as any
other historical fact captured in it. Disclosed plainly on the
deletion confirmation page rather than silently ignored — the honest
alternative to a guarantee this action structurally can't make.

## Data export (GDPR)

Profile → "Download your data" (`apps.accounts.views.DataExportView`,
`apps.accounts.services.export_account_data`) — self-service exercise
of GDPR Article 20 ("right to data portability"), the natural
companion to account deletion above: knowing you *can* get a copy of
everything first is part of what makes erasure a safe thing to do.

Covers the same set of models account deletion hard-deletes (workout
history, personal records, measurements, nutrition data, activities,
feedback, API key metadata) plus this user's own authored shared
content (a custom exercise/food/recipe/program/meal slot they
created) — a read of what exists today, not a statement about what
deletion would do to each of it. Built on Django's own generic model
serializer rather than a hand-maintained field list per model, so a
field added to any of these models later is included automatically
without this feature needing an update to match.

**`ApiKey.key_hash` is the one deliberate field-level exclusion.**
Every other model is exported with every field via the generic
serializer above; `ApiKey`'s own export is hand-built instead, listing
every field except that one explicitly — a key's hash is a
credential, not data to read back, the same reason a password hash is
never shown to its own owner either.

Available as a plain HTML page (reusing this app's own templates,
something to actually read rather than only archive), a single JSON
file (the same shape a user's own API key could already fetch), or a
`.zip` of one CSV file per section (opens directly in a spreadsheet)
— three views of the exact same underlying export, not three
different exports to keep in sync.

## Auditability

For important destructive or irreversible operations, consider preserving historical records instead of deleting them — full account deletion above is the one deliberate exception, since GDPR erasure only means something if it's genuinely irreversible.

Workout history should be treated as durable data.
