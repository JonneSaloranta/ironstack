from .base import *  # noqa: F401,F403
from .base import env, env_bool, env_list

DEBUG = False

env("DJANGO_ALLOWED_HOSTS", required=True)  # fail fast if unset
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS")

# Derived from ALLOWED_HOSTS rather than a separate env var — every
# host this instance is reachable at is, by definition, also a host a
# real POST request to it should be trusted to have come from. Without
# this, Django 4+'s CSRF check (which compares the request's Origin/
# Referer against this list for HTTPS requests) can reject legitimate
# form submissions with "CSRF verification failed" the moment anything
# about the reverse proxy/TLS setup doesn't line up exactly with what
# Django infers on its own — a common footgun for exactly this
# proxied-Django deployment shape. ALLOWED_HOSTS' own leading-dot
# wildcard syntax (".example.com" matches any subdomain) maps to
# CSRF_TRUSTED_ORIGINS' "https://*.example.com" equivalent.
CSRF_TRUSTED_ORIGINS = [
    f"https://*{host}" if host.startswith(".") else f"https://{host}"
    for host in ALLOWED_HOSTS
]

SECRET_KEY = env("DJANGO_SECRET_KEY", required=True)

# Security — see docs/SECURITY.md.
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_SSL_REDIRECT = env_bool("DJANGO_SECURE_SSL_REDIRECT", default=True)
SECURE_HSTS_SECONDS = 60 * 60 * 24 * 7
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# The reverse proxy terminates TLS and forwards this header.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
