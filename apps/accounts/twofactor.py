"""Two-factor authentication (TOTP, RFC 6238) — a single authenticator
per user (apps.accounts.models.User.totp_secret/totp_enabled) plus a
set of one-time backup/recovery codes (TwoFactorBackupCode). See
docs/SECURITY.md "Two-factor authentication" for the storage trade-offs
(the secret is plain text, not encrypted at rest) and rate-limiting
(apps.accounts.forms.RateLimitedTwoFactorForm) this deliberately
narrow module doesn't handle itself.

pyotp does the actual RFC 6238 math — this module is just the thin
IronStack-specific layer on top: how many backup codes, how they're
generated/hashed/consumed, and the provisioning URI's issuer name.
"""

import base64
import secrets
from io import BytesIO

import pyotp
import qrcode
from django.contrib.auth.hashers import check_password, make_password
from django.utils import timezone

from .models import TwoFactorBackupCode

BACKUP_CODE_COUNT = 10
# 4 groups of 4 hex characters ("a1b2-c3d4-e5f6-1234") — long enough to
# not be practically guessable (16^16 possibilities), short enough to
# type by hand if a password manager isn't available for it, and
# visually distinct at a glance from a 6-digit TOTP code (the same
# text input on the verify page tries TOTP first only when the
# submitted value is 6 plain digits, falling back to a backup-code
# lookup otherwise — see apps.accounts.views.TwoFactorVerifyView).
_BACKUP_CODE_GROUPS = 4
_BACKUP_CODE_GROUP_LENGTH = 4


def generate_totp_secret():
    """A fresh base32 secret — pyotp's own recommended entropy/length.
    Called once when 2FA setup starts (apps.accounts.views.
    TwoFactorSetupView's GET) and stored on the user immediately, even
    before they've confirmed it, so the QR code shown and the code
    they submit to confirm are generated from the same value."""
    return pyotp.random_base32()


def provisioning_uri(user, secret):
    """The otpauth:// URI an authenticator app's QR scanner reads —
    "IronStack" as the issuer so it's labeled sensibly in the app
    alongside whatever other accounts a user has there."""
    return pyotp.TOTP(secret).provisioning_uri(name=user.username, issuer_name="IronStack")


def qr_code_data_uri(uri):
    """`uri` (provisioning_uri()'s output) rendered as a PNG and
    returned as a data: URI — embedded directly in the setup page's
    own <img src>, no separate image-serving endpoint/request needed
    (which would otherwise have to re-derive the same not-yet-confirmed
    secret from somewhere, e.g. the session, for no real benefit)."""
    img = qrcode.make(uri)
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def verify_totp_code(secret, code):
    """valid_window=1 tolerates the code from one 30s step before/after
    the server's own clock — real authenticator/server clocks drift a
    few seconds against each other even when both are honestly NTP-
    synced, and without this a code entered right at a 30s boundary
    would intermittently, confusingly fail."""
    return pyotp.TOTP(secret).verify(code, valid_window=1)


def generate_backup_codes(user):
    """Replaces this user's entire set of backup codes and returns the
    new ones in plain text — the only time they're ever available in
    that form; only a hash of each is stored (TwoFactorBackupCode.
    code_hash, Django's own password hasher — see that model's own
    docstring for why not a fast digest). Called both when 2FA is first
    confirmed and whenever the user deliberately regenerates them
    (Profile → Two-factor authentication → "Regenerate backup codes"),
    which is also the only recovery path if they're ever used up or
    lost without disabling 2FA first."""
    TwoFactorBackupCode.objects.filter(user=user).delete()
    codes = []
    for _ in range(BACKUP_CODE_COUNT):
        code = "-".join(
            secrets.token_hex(_BACKUP_CODE_GROUP_LENGTH // 2) for _ in range(_BACKUP_CODE_GROUPS)
        )
        codes.append(code)
        TwoFactorBackupCode.objects.create(user=user, code_hash=make_password(code))
    return codes


def verify_and_consume_backup_code(user, submitted_code):
    """Checks `submitted_code` against every one of this user's still-
    unused backup codes (there's no shortcut lookup — code_hash is a
    one-way hash, the same reason a login can't look up a user by
    password), and marks the matching one used on success so it can
    never be reused. Returns whether it matched."""
    for backup_code in user.backup_codes.filter(used_at__isnull=True):
        if check_password(submitted_code, backup_code.code_hash):
            backup_code.used_at = timezone.now()
            backup_code.save(update_fields=["used_at"])
            return True
    return False
