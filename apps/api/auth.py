from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed

from . import crypto
from .models import ApiKey

BEARER = b"bearer"


class ApiKeyAuthentication(BaseAuthentication):
    """`Authorization: Bearer <key>` — the only auth scheme apps.api
    accepts (no session/cookie auth: this is a machine-to-machine API,
    not a second way to drive the browser UI — see config.settings.base's
    REST_FRAMEWORK comment). Hashes the presented secret and looks it up
    by `key_hash`, never by comparing raw strings — apps.api.crypto's own
    docstring covers why SHA-256 (not a slow password hasher) is the
    right choice here.

    `request.api_key` is stashed on success so
    apps.api.permissions.HasContextPermission and apps.api.throttling
    can read it without a second lookup — DRF's `request.auth` carries
    the same object too (the second element of the returned tuple below)
    for anything that prefers going through the standard DRF surface.
    """

    def authenticate(self, request):
        auth_header = get_authorization_header(request).split()
        if not auth_header or auth_header[0].lower() != BEARER:
            # No/unrecognized scheme -> let other authenticators (or
            # anonymous access) handle it.
            return None
        if len(auth_header) != 2:
            raise AuthenticationFailed("Malformed Authorization header.")

        raw_secret = auth_header[1].decode("utf-8", errors="ignore")
        key_hash = crypto.hash_secret(raw_secret)
        try:
            api_key = ApiKey.objects.select_related("user", "tier").get(
                key_hash=key_hash, is_active=True
            )
        except ApiKey.DoesNotExist:
            raise AuthenticationFailed("Invalid or inactive API key.") from None

        api_key.last_used_at = timezone.now()
        api_key.save(update_fields=["last_used_at"])
        request.api_key = api_key
        return (api_key.user, api_key)

    def authenticate_header(self, request):
        return "Bearer"
