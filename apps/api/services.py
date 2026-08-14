"""API key issuance/permission logic — kept out of views per CLAUDE.md.

Shared by the self-service key-management pages (apps.api.views_web) and
the Django admin (apps.api.admin), so both ever only go through one path
for "create a key"/"revoke a key" rather than each re-deriving the
max-keys check or the permission-row bookkeeping.
"""

from django.db import transaction

from . import crypto
from .models import ApiContext, ApiKey, ApiKeyPermission, ApiSettings, RateLimitTier


def api_keys_for(user):
    return ApiKey.objects.filter(user=user).select_related("tier")


def remaining_key_quota(user):
    """How many more keys `user` may create right now — never negative."""
    max_keys = ApiSettings.load().max_api_keys_per_user
    return max(0, max_keys - api_keys_for(user).count())


def default_tier():
    """The tier newly created keys get, unless changed later by an admin
    (apps.api.admin.ApiKeyAdmin) — falls back to whichever tier sorts
    first if no admin has flagged one `is_default` yet (e.g. a fresh
    install before the seed migration's tier exists for some reason),
    so key creation never hard-fails just because that flag is unset.
    """
    return RateLimitTier.objects.filter(is_default=True).first() or RateLimitTier.objects.first()


@transaction.atomic
def create_api_key(user, *, name, permissions):
    """Creates a new key for `user`, raising ValueError if they're
    already at their quota (apps.api.forms.ApiKeyCreateForm surfaces
    this as a normal form error before ever calling this, but the check
    lives here too since the Django admin's "add" flow doesn't go
    through that form at all).

    `permissions`: a dict of `{ApiContext value: {"can_create": bool,
    "can_read": bool, "can_update": bool, "can_delete": bool}}` — every
    context not present defaults to no access at all, matching a form
    field simply being left unchecked.

    Returns `(api_key, raw_secret)` — `raw_secret` is the only time the
    real credential is ever available; nothing persists it (see
    apps.api.crypto's own docstring).
    """
    if remaining_key_quota(user) <= 0:
        raise ValueError("You've reached your maximum number of API keys.")

    raw_secret, prefix, key_hash = crypto.generate_secret()
    api_key = ApiKey.objects.create(
        user=user, name=name, key_hash=key_hash, prefix=prefix, tier=default_tier()
    )
    set_permissions(api_key, permissions)
    return api_key, raw_secret


def set_permissions(api_key, permissions):
    """Upserts one ApiKeyPermission row per ApiContext for `api_key` —
    always all 8, even when every flag on a given context is False, so
    "no access to this context" is an explicit, visible row rather than
    an absent one (apps.api.permissions still treats a missing row and
    an all-False row identically; this is purely so the key's own detail
    page/admin view shows every context, not just the granted ones).
    """
    for context in ApiContext.values:
        granted = permissions.get(context, {})
        ApiKeyPermission.objects.update_or_create(
            api_key=api_key,
            context=context,
            defaults={
                "can_create": bool(granted.get("can_create")),
                "can_read": bool(granted.get("can_read")),
                "can_update": bool(granted.get("can_update")),
                "can_delete": bool(granted.get("can_delete")),
            },
        )


def revoke_api_key(api_key):
    """Permanently removes a key — it's a credential, not training
    history, so unlike the rest of this app's "never hard-delete, use
    active=False" convention (docs/DOMAIN_MODEL.md), there's no later
    record that needs it to keep existing, and "revoke" should mean the
    secret stops being valid outright, not just hidden."""
    api_key.delete()
