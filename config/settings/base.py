"""
Base settings shared by all environments.

Environment-driven configuration uses plain `os.environ` (no extra
dependency) — see the small helpers below. Environment-specific settings
(DEBUG, ALLOWED_HOSTS, security flags, ...) live in `dev.py`/`production.py`.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def env(key, default=None, required=False):
    value = os.environ.get(key, default)
    if required and value is None:
        raise RuntimeError(f"Required environment variable {key} is not set")
    return value


def env_bool(key, default=False):
    value = os.environ.get(key)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def env_list(key, default=""):
    value = os.environ.get(key, default)
    return [item.strip() for item in value.split(",") if item.strip()]


def env_admins(key, default=""):
    """Parses `"Name:email,Name2:email2"` into the `[(name, email), ...]`
    pairs Django's ADMINS/MANAGERS settings expect."""
    admins = []
    for entry in env_list(key, default):
        name, _, address = entry.partition(":")
        if address:
            admins.append((name.strip(), address.strip()))
    return admins


SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-secret-key-change-me")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    # apps.accounts.oidc / AUTHENTIK_* settings below — always
    # installed (it defines no models/migrations, so there's no cost
    # to an instance that never configures Authentik), but its
    # views/backend only ever get exercised once AUTHENTIK_ENABLED is
    # True. See docs/SECURITY.md "Single sign-on (Authentik / OIDC)".
    "mozilla_django_oidc",
    # IronStack apps
    "apps.core",
    "apps.accounts",
    "apps.exercises",
    "apps.programs",
    "apps.workouts",
    "apps.records",
    "apps.progression",
    "apps.measurements",
    "apps.activities",
    "apps.analytics",
    "apps.nutrition",
    "apps.api",
    "apps.social",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Right next to SecurityMiddleware, which sets the other response-
    # level security headers (X-Content-Type-Options, HSTS, ...) —
    # see apps.core.middleware.ContentSecurityPolicyMiddleware's own
    # docstring and docs/SECURITY.md "Content-Security-Policy" for the
    # policy itself. Applied in every environment, not just production
    # — a dev-only violation (e.g. a template that grew a stray
    # external <script src>) is far easier to notice and fix locally
    # than to discover it's been silently missing in prod all along.
    "apps.core.middleware.ContentSecurityPolicyMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    # Must come after AuthenticationMiddleware (needs request.user) but
    # still runs before the view/template rendering, so it can override
    # LocaleMiddleware's own guess (cookie/Accept-Language) with a
    # logged-in user's stored preference (apps.accounts.models.User.language,
    # set on the profile page) for this same request/response — see the
    # middleware's own docstring.
    "apps.accounts.middleware.UserLanguageMiddleware",
    # Same after-auth, before-view placement as UserLanguageMiddleware
    # just above — see apps.accounts.middleware.UserTimezoneMiddleware's
    # own docstring for what this fixes.
    "apps.accounts.middleware.UserTimezoneMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "django.template.context_processors.i18n",
                "apps.workouts.context_processors.active_workout_session",
                "apps.core.context_processors.app_version",
                "apps.core.context_processors.admin_contact",
                "apps.core.context_processors.seo",
                "apps.core.context_processors.push",
                "apps.accounts.context_processors.onboarding",
                "apps.nutrition.context_processors.nutrition_subnav",
                "apps.social.context_processors.social_badge",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="ironstack"),
        "USER": env("POSTGRES_USER", default="ironstack"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="ironstack"),
        "HOST": env("POSTGRES_HOST", default="localhost"),
        "PORT": env("POSTGRES_PORT", default="5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LOGIN_URL = "login"
LOGIN_REDIRECT_URL = "dashboard"
LOGOUT_REDIRECT_URL = "login"

# ModelBackend must stay listed even when Authentik SSO is configured
# below — it's what django.contrib.auth's own AuthenticationForm (via
# apps.accounts.forms.RateLimitedAuthenticationForm) authenticates
# local username/password logins against.
# apps.accounts.oidc.IronStackOIDCAuthenticationBackend is appended
# further down, only once AUTHENTIK_ENABLED is actually True — see
# docs/SECURITY.md "Single sign-on (Authentik / OIDC)".
AUTHENTICATION_BACKENDS = ["django.contrib.auth.backends.ModelBackend"]

# UI language is a per-user preference (User.language, set on the
# profile page — apps.accounts.middleware.UserLanguageMiddleware applies
# it) using Django's own gettext .po/.mo translation machinery. This is
# a distinct concern from unit_system/timezone, which stay
# apps.core/apps.accounts preferences unrelated to Django's i18n
# framework — display units and "what time is it" don't change with UI
# language.
LANGUAGE_CODE = "en-us"
LANGUAGES = [
    ("en", "English"),
    ("fi", "Suomi"),
    ("sv", "Svenska"),
    ("ru", "Русский"),
    ("it", "Italiano"),
    ("et", "Eesti"),
]
LOCALE_PATHS = [BASE_DIR / "locale"]
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Backs both Django's generic cache API and, more importantly here,
# apps.api's rate-limit throttling (DRF's throttle classes read/write
# request counters through this same cache). Production runs multiple
# gunicorn workers (docker-compose.yml, --workers 3) as separate
# processes with no shared memory, so Django's default LocMemCache would
# give each worker its own independent counter — a key's real allowed
# throughput would end up worker_count times its configured limit,
# silently. The database cache backend is a real shared store without
# adding Redis or any other new infrastructure dependency; the table it
# needs is created by `manage.py createcachetable`, wired into
# docker-compose's startup command right after `migrate` the same way
# `compilemessages` already is.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.db.DatabaseCache",
        "LOCATION": "django_cache",
    }
}

# apps.api — see docs/API.md. Authentication/permission/throttling are
# all API-key-driven (apps.api.auth/permissions/throttling), never
# session/cookie auth — this is a machine-to-machine API, not a second
# way to drive the same server-rendered UI a browser uses.
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["apps.api.auth.ApiKeyAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["apps.api.permissions.HasContextPermission"],
    "DEFAULT_THROTTLE_CLASSES": [
        "apps.api.throttling.ApiKeyMinuteThrottle",
        "apps.api.throttling.ApiKeyDayThrottle",
    ],
    # Both throttle classes compute their actual rate per-request from
    # the authenticated key's own tier (see apps.api.throttling) — these
    # DEFAULT_THROTTLE_RATES entries exist only because DRF's
    # SimpleRateThrottle requires *some* configured rate to key its
    # per-scope cache namespace by; the real numbers never come from here.
    "DEFAULT_THROTTLE_RATES": {"api_key_minute": "1000/min", "api_key_day": "1000000/day"},
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DATETIME_FORMAT": "iso-8601",
}

# docs/SECURITY.md "Email" — needed for password reset
# (django.contrib.auth's own PasswordResetView, wired in config.urls)
# and, via ADMINS below, Django's built-in mail_admins error reporting.
# Defaults to the console backend (prints the message instead of
# sending it) whenever DJANGO_EMAIL_HOST isn't set, rather than failing
# — password reset still "works" enough to unblock local development,
# it just doesn't deliver anywhere real until SMTP is actually
# configured. See docs/SECURITY.md before relying on this in
# production.
EMAIL_HOST = env("DJANGO_EMAIL_HOST", default="")
if EMAIL_HOST:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
    EMAIL_PORT = int(env("DJANGO_EMAIL_PORT", default="587"))
    EMAIL_HOST_USER = env("DJANGO_EMAIL_HOST_USER", default="")
    EMAIL_HOST_PASSWORD = env("DJANGO_EMAIL_HOST_PASSWORD", default="")
    EMAIL_USE_TLS = env_bool("DJANGO_EMAIL_USE_TLS", default=True)
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"
DEFAULT_FROM_EMAIL = env("DJANGO_DEFAULT_FROM_EMAIL", default="IronStack <noreply@localhost>")
# Django's SMTP backend uses this as the envelope sender for its own
# mail_admins() error reports (below) when nothing more specific is set.
SERVER_EMAIL = DEFAULT_FROM_EMAIL

# Whoever's listed here gets an email — via EMAIL_BACKEND above — for
# every uncaught exception once DEBUG=False (config.settings.production),
# Django's own built-in behavior (django.utils.log.AdminEmailHandler,
# wired by Django's default LOGGING config — no override needed, no new
# dependency like Sentry required for this baseline). Format:
# "Name:email,Name2:email2". Empty by default, so nothing breaks if
# it's never set — just no one gets notified of errors, the same
# silence as before this existed.
ADMINS = env_admins("DJANGO_ADMINS")
MANAGERS = ADMINS

# A public-facing "who runs this instance, and how do I reach them"
# address — shown in the privacy notice modal (templates/accounts/
# _privacy_notice_modal.html, reachable from the login/signup pages
# and Profile), which already tells a user "the operator, not this
# project's authors, is who you should contact about any of the
# below" without ever actually saying how. Deliberately a separate
# setting from ADMINS above rather than reusing its first address:
# ADMINS is Django's own uncaught-exception mailing list (ops-facing,
# potentially several addresses), this is one address meant for a
# user to read and act on — the same instance could reasonably want
# crash reports going to one inbox and privacy/data requests to
# another. Empty by default, so an instance that never sets it just
# doesn't show a contact address, same silence as ADMINS unset.
ADMIN_CONTACT_EMAIL = env("DJANGO_ADMIN_CONTACT_EMAIL", default="")

# docs/SECURITY.md — self-hosted instances aren't necessarily meant to
# accept public registration; set to false once the intended users have
# accounts, or from the start if the instance is reachable beyond a
# trusted network. Existing users can keep logging in either way, since
# only SignupView reads this.
SIGNUP_ENABLED = env_bool("DJANGO_SIGNUP_ENABLED", default=True)

# Independent of SIGNUP_ENABLED above: this gates the local username/
# password *login* form itself (apps.accounts.views.RateLimitedLoginView/
# RateLimitedPasswordResetView/SignupView — see docs/SECURITY.md
# "Single sign-on (Authentik / OIDC)"), for an operator who wants
# Authentik as the only way in once every intended user has an SSO-
# linked account. False also closes signup and password reset, since
# neither makes sense without a usable local password. Existing
# accounts with a local password simply can't use it to log in while
# this is off — nothing about the account itself changes, so setting
# it back to True restores password login for them exactly as before.
# /admin/ is deliberately unaffected — it's Django's own separate
# login view (see apps.core.admin), kept as a break-glass path so a
# misconfigured Authentik instance can never lock every admin out of
# this app at once.
PASSWORD_LOGIN_ENABLED = env_bool("DJANGO_PASSWORD_LOGIN_ENABLED", default=True)

# Authentik single sign-on (docs/SECURITY.md "Single sign-on (Authentik
# / OIDC)") — an OpenID Connect Relying Party against an
# externally-run Authentik instance, via mozilla-django-oidc
# (apps.accounts.oidc). Entirely optional: leave AUTHENTIK_URL unset
# and none of this activates — no extra INSTALLED_APPS surface beyond
# the app being present (it defines no models), no new urls.py routes,
# no "Log in with Authentik" button, AUTHENTICATION_BACKENDS stays
# ModelBackend-only. AUTHENTIK_URL is this Authentik instance's own
# base URL (e.g. "https://auth.example.com", no trailing slash);
# AUTHENTIK_CLIENT_ID/AUTHENTIK_CLIENT_SECRET come from the OAuth2/
# OpenID provider created for this app in Authentik (Admin interface →
# Applications → Providers → create "OAuth2/OpenID Provider", redirect
# URI f"{this app's own base URL}/oidc/callback/").
AUTHENTIK_URL = env("AUTHENTIK_URL", default="")
AUTHENTIK_CLIENT_ID = env("AUTHENTIK_CLIENT_ID", default="")
AUTHENTIK_CLIENT_SECRET = env("AUTHENTIK_CLIENT_SECRET", default="")
# Optional: how *this app's own server* reaches Authentik, if that's a
# different address than AUTHENTIK_URL (what the user's browser is
# redirected to). Defaults to AUTHENTIK_URL — most deployments only
# need one address. Diverges when Authentik runs as a separate Docker
# stack: the browser needs Authentik's externally published address,
# but this app's own container usually can't reach that same address
# from inside its own network namespace ("localhost"/a published port
# means something different from inside a container than from the
# host) — pointing this at Authentik's container hostname on a shared
# Docker network (e.g. "http://authentik-server:9000") solves that
# without changing what the browser is ever redirected to.
AUTHENTIK_INTERNAL_URL = env("AUTHENTIK_INTERNAL_URL", default="") or AUTHENTIK_URL
# Which Authentik "application" slug's own per-application OIDC issuer
# to use (Authentik's issuer_mode="per_provider" default mints a
# distinct issuer/JWKS per application at
# f"{AUTHENTIK_URL}/application/o/{slug}/..." rather than one shared
# endpoint) — matches whatever slug the application was actually
# created with in Authentik, not necessarily "ironstack".
AUTHENTIK_APPLICATION_SLUG = env("AUTHENTIK_APPLICATION_SLUG", default="ironstack")
# Optional extra access gate (apps.accounts.oidc.
# IronStackOIDCAuthenticationBackend.verify_claims) — an Authentik
# group name a user must belong to for "Log in with Authentik" to
# actually let them in. Unset (the default) means having *any*
# Authentik account is enough, which matters on a shared Authentik
# instance that also authenticates other, unrelated applications.
# Defense in depth alongside (not instead of) restricting the
# Application itself in Authentik's own admin UI (Applications →
# Policy / Group / User Bindings) to the same group.
AUTHENTIK_REQUIRED_GROUP = env("AUTHENTIK_REQUIRED_GROUP", default="")
AUTHENTIK_ENABLED = bool(AUTHENTIK_URL and AUTHENTIK_CLIENT_ID and AUTHENTIK_CLIENT_SECRET)

if AUTHENTIK_ENABLED:
    AUTHENTICATION_BACKENDS.append("apps.accounts.oidc.IronStackOIDCAuthenticationBackend")

    OIDC_RP_CLIENT_ID = AUTHENTIK_CLIENT_ID
    OIDC_RP_CLIENT_SECRET = AUTHENTIK_CLIENT_SECRET
    # RS256, not mozilla-django-oidc's own HS256 default — Authentik
    # signs id_tokens with its own per-provider RSA key (see the
    # OIDC_OP_JWKS_ENDPOINT below, fetched to verify that signature)
    # rather than the shared client secret HS256 would use.
    OIDC_RP_SIGN_ALGO = "RS256"
    # Browser-facing: the user's own browser is redirected here
    # directly, so this must be whatever address they can actually
    # reach — always AUTHENTIK_URL.
    OIDC_OP_AUTHORIZATION_ENDPOINT = f"{AUTHENTIK_URL}/application/o/authorize/"
    # Server-facing: this app's own backend calls these directly
    # (mozilla_django_oidc.auth.OIDCAuthenticationBackend's token
    # exchange, JWKS fetch, userinfo fetch) — AUTHENTIK_INTERNAL_URL,
    # which is just AUTHENTIK_URL again unless overridden above.
    _authentik_internal_issuer = (
        f"{AUTHENTIK_INTERNAL_URL}/application/o/{AUTHENTIK_APPLICATION_SLUG}"
    )
    OIDC_OP_TOKEN_ENDPOINT = f"{AUTHENTIK_INTERNAL_URL}/application/o/token/"
    OIDC_OP_USER_ENDPOINT = f"{AUTHENTIK_INTERNAL_URL}/application/o/userinfo/"
    OIDC_OP_JWKS_ENDPOINT = f"{_authentik_internal_issuer}/jwks/"
    OIDC_RP_SCOPES = "openid email profile"
    # mozilla_django_oidc's own default is "/" — a login rejected by
    # IronStackOIDCAuthenticationBackend.verify_claims (e.g.
    # AUTHENTIK_REQUIRED_GROUP set and the user isn't a member) should
    # land back on the login page, not the dashboard URL an anonymous
    # visitor would just get redirected away from again anyway.
    LOGIN_REDIRECT_URL_FAILURE = "login"
    # PKCE on top of the authorization-code flow's own state/nonce —
    # cheap extra protection against an authorization code being
    # intercepted, and something Authentik supports out of the box
    # (see the discovery document's code_challenge_methods_supported).
    OIDC_USE_PKCE = True

# apps.core.push — Web Push notifications for messages
# (docs/SECURITY.md "Web Push notifications"). Entirely optional: leave
# VAPID_PUBLIC_KEY/VAPID_PRIVATE_KEY unset and PUSH_ENABLED stays
# False — no "Notifications" card on the profile page, no push ever
# sent, apps.core.push.send_push_notification becomes a no-op. Both
# keys are base64url-encoded with no PEM headers, single line each —
# generate a pair with `manage.py generate_vapid_keys` (run once, keep
# the private key secret). VAPID_ADMIN_EMAIL is the contact address a
# push service sees in the VAPID JWT's "sub" claim (RFC 8292) if it
# ever needs to reach the operator about abuse — any real address
# works, it's never shown to end users.
VAPID_PUBLIC_KEY = env("VAPID_PUBLIC_KEY", default="")
VAPID_PRIVATE_KEY = env("VAPID_PRIVATE_KEY", default="")
VAPID_ADMIN_EMAIL = env("VAPID_ADMIN_EMAIL", default="")
PUSH_ENABLED = bool(VAPID_PUBLIC_KEY and VAPID_PRIVATE_KEY and VAPID_ADMIN_EMAIL)

# apps.core.management.commands.backup_scheduler — docs/BACKUP.md.
# UTC hour (0-23) the docker-compose.yml `backup-scheduler` service
# runs `create_backup` at, once a day.
BACKUP_HOUR = int(env("BACKUP_HOUR", default="3"))
