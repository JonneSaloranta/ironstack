"""Request-derived helpers shared across apps that need to key
something (a rate-limit counter, an audit log) by the actual client,
not this app's own reverse proxy.
"""


def client_ip(request):
    """The `X-Real-IP` header `compose/nginx/nginx.conf` sets to
    `$remote_addr` — nginx overwrites this unconditionally rather than
    forwarding whatever a client sent, so it can't be spoofed by a
    request that goes through that proxy. `REMOTE_ADDR` on its own
    would be nginx's *own* container IP for every proxied request
    (docker-compose.yml never publishes a port for `web` directly —
    only `nginx` is reachable from outside), which would make every
    visitor share one counter. Falls back to REMOTE_ADDR for direct,
    no-proxy access (e.g. `runserver` in dev).

    Originally lived only in apps.accounts.forms (login/password-reset
    rate limiting); pulled out here once apps.social needed the exact
    same logic for its invite-code lookup throttle, rather than a
    second copy or a cross-app import of a private helper.
    """
    return request.META.get("HTTP_X_REAL_IP") or request.META.get("REMOTE_ADDR", "unknown")
