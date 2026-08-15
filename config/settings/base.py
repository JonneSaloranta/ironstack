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
    "apps.api",
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

# docs/SECURITY.md — self-hosted instances aren't necessarily meant to
# accept public registration; set to false once the intended users have
# accounts, or from the start if the instance is reachable beyond a
# trusted network. Existing users can keep logging in either way, since
# only SignupView reads this.
SIGNUP_ENABLED = env_bool("DJANGO_SIGNUP_ENABLED", default=True)

# apps.core.management.commands.backup_scheduler — docs/BACKUP.md.
# UTC hour (0-23) the docker-compose.yml `backup-scheduler` service
# runs `create_backup` at, once a day.
BACKUP_HOUR = int(env("BACKUP_HOUR", default="3"))
