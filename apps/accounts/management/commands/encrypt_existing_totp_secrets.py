from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User


class Command(BaseCommand):
    """Re-saves every user's `totp_secret` so EncryptedTextField
    (apps.accounts.models) actually encrypts it — see that field's own
    docstring for why turning TOTP_ENCRYPTION_KEY on doesn't do this
    by itself (only a row's *next* save does). Run once, right after
    setting TOTP_ENCRYPTION_KEY in .env and restarting — safe to run
    again later too (a row already encrypted under the current key
    round-trips through save() unchanged, decrypted then re-encrypted
    to the same ciphertext-worth of protection)."""

    help = "Encrypt every existing User.totp_secret with the now-configured TOTP_ENCRYPTION_KEY."

    def handle(self, *args, **options):
        if not settings.TOTP_ENCRYPTION_KEY:
            raise CommandError(
                "TOTP_ENCRYPTION_KEY isn't set — nothing to encrypt with. Set it in .env "
                "and restart first."
            )
        # exclude("") — nothing to do for a user who never set up 2FA
        # at all (totp_secret defaults to ""), and re-saving those rows
        # too would just be wasted writes.
        users = User.objects.exclude(totp_secret="")
        count = 0
        for user in users:
            user.save(update_fields=["totp_secret"])
            count += 1
        self.stdout.write(self.style.SUCCESS(f"Encrypted totp_secret for {count} user(s)."))
