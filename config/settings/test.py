"""Settings for the test suite (pyproject.toml's DJANGO_SETTINGS_MODULE).

Everything as in development, except what only makes tests slow. Never
used to run the app."""

from .dev import *  # noqa: F401,F403

# Django's default password hasher is deliberately slow (about a second
# per hash on the dev machine), and almost every test creates a user and
# logs in - that alone was most of a 50-minute suite. Tests don't need
# the hash to resist cracking, so use the fastest one Django ships.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# apps.core.backup_tasks - run "Create backup"/"Restore" inside the
# request so view tests see the outcome without a background process.
BACKUP_TASKS_RUN_INLINE = True
