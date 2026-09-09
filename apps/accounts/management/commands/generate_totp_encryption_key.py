from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """One-time key generation for TOTP secret encryption
    (docs/SECURITY.md "Two-factor authentication") — a self-hoster
    runs this once, pastes the printed line into their .env, restarts,
    then runs `manage.py encrypt_existing_totp_secrets` once to
    encrypt any secret already stored from before. Never run again on
    a live instance: re-running this and switching to the new key
    breaks 2FA login for every user whose secret is still encrypted
    under the *old* one (EncryptedTextField reads it back as garbage,
    the same "losing the key" failure this project's other
    generate_*_key commands already warn about).
    """

    help = "Generate a new TOTP secret encryption key and print it as a .env line."

    def handle(self, *args, **options):
        key = Fernet.generate_key().decode()
        self.stdout.write("TOTP_ENCRYPTION_KEY=" + key)
        self.stdout.write(
            self.style.WARNING(
                "Paste this line into your .env, restart, then run 'manage.py "
                "encrypt_existing_totp_secrets' once. Keep this key as secret as "
                "SECRET_KEY — losing it breaks 2FA login for every user who has it enabled."
            )
        )
