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


SECRET_KEY = env("DJANGO_SECRET_KEY", default="dev-insecure-secret-key-change-me")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
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
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
