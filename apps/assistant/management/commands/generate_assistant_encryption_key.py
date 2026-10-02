from cryptography.fernet import Fernet
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    """Prints a fresh Fernet key for ASSISTANT_ENCRYPTION_KEY
    (config.settings.base) — what users' own saved API keys are encrypted
    with. Changing it later makes every saved key unreadable; users then
    simply enter theirs again (apps.assistant's settings page says the key
    was rejected)."""

    help = "Print a new ASSISTANT_ENCRYPTION_KEY value."

    def handle(self, *args, **options):
        self.stdout.write(f"ASSISTANT_ENCRYPTION_KEY={Fernet.generate_key().decode()}")
