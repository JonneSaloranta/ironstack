from .base import *  # noqa: F401,F403
from .base import env_list

DEBUG = True

# Wide open by default in dev so the app is reachable from a phone/other
# device on the LAN (e.g. mobile-first UI testing) without extra config.
# Override via DJANGO_ALLOWED_HOSTS if you want it locked down locally.
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", default="*")
