from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """One-time key generation for backup encryption (docs/BACKUP.md
    "Encryption") — a self-hoster runs this once and pastes the
    printed line into their .env, then restarts. Never run
    automatically: re-running this doesn't touch any already-created
    backup, but every backup made under the *old* key becomes
    unrestorable the moment it's discarded in favor of a new one, the
    same way VAPID_* keys work (see generate_vapid_keys' own
    docstring) — restore_backup() needs the exact key a given archive
    was encrypted with, not just "a" key.
    """

    help = "Generate a new backup encryption key and print it as a .env line."

    def handle(self, *args, **options):
        key = Fernet.generate_key().decode()
        self.stdout.write("BACKUP_ENCRYPTION_KEY=" + key)
        self.stdout.write(
            self.style.WARNING(
                "Paste this line into your .env, then restart. Keep it as secret as "
                "SECRET_KEY/VAPID_PRIVATE_KEY, and back it up somewhere that isn't only "
                "inside a backup encrypted with it — losing this key makes every backup "
                "made while it was set permanently unrestorable."
            )
        )
