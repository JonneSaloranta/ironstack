from base64 import urlsafe_b64encode

from cryptography.hazmat.primitives import serialization
from django.core.management.base import BaseCommand
from py_vapid import Vapid


class Command(BaseCommand):
    """One-time key generation for Web Push (docs/SECURITY.md "Web
    Push notifications") — a self-hoster runs this once and pastes the
    two printed lines into their .env, then restarts. Never run
    automatically (no startup-command hook, unlike e.g.
    createcachetable): re-running this invalidates every existing
    PushSubscription's stored key material, silently breaking push for
    every already-subscribed device until each one re-subscribes.

    Prints both keys base64url-encoded with no PEM headers, single
    line each — the one format py_vapid.Vapid.from_string() correctly
    auto-detects from a plain string (this project's env() helper
    doesn't support multi-line values, ruling out PEM), and, for the
    public key, also exactly the raw-uncompressed-point form the
    browser's PushManager.subscribe({applicationServerKey}) needs
    client-side — one value, zero reformatting between settings.py and
    the page it's rendered onto.
    """

    help = "Generate a new VAPID keypair for Web Push notifications and print it as .env lines."

    def handle(self, *args, **options):
        vapid = Vapid()
        vapid.generate_keys()

        private_raw = vapid.private_key.private_numbers().private_value.to_bytes(32, "big")
        public_raw = vapid.public_key.public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )

        private_key = urlsafe_b64encode(private_raw).rstrip(b"=").decode()
        public_key = urlsafe_b64encode(public_raw).rstrip(b"=").decode()
        self.stdout.write("VAPID_PRIVATE_KEY=" + private_key)
        self.stdout.write("VAPID_PUBLIC_KEY=" + public_key)
        self.stdout.write(
            self.style.WARNING(
                "Paste both lines into your .env, set VAPID_ADMIN_EMAIL too, then restart. "
                "Keep the private key secret — anyone with it can send push notifications "
                "impersonating this instance to every subscribed device."
            )
        )
