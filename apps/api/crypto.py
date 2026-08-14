"""API key secret generation/hashing — isolated from apps.api.services so
the one place that ever touches the raw secret string is small and
easy to audit.

A key isn't a password (nothing about it needs to be memorable, and it's
generated with far more entropy than any human-chosen password would
have), so this deliberately does *not* use Django's slow, salted
password hashers (PBKDF2/Argon2, tuned to resist guessing a low-entropy
secret) — a fast, unsalted SHA-256 digest is the standard, correct
choice for a high-entropy random token (the same approach GitHub/Stripe
use for their own API keys): the secret can't be brute-forced from its
hash regardless of hashing speed, so slowing down verification would
only cost real request latency for no security benefit.
"""

import hashlib
import secrets

KEY_PREFIX = "isk"  # "IronStack Key" — lets a leaked secret be identified at a glance


def generate_secret():
    """Returns (raw_secret, prefix, key_hash). `raw_secret` is shown to
    the user exactly once and never stored; `key_hash` is what
    apps.api.models.ApiKey.key_hash persists and apps.api.auth looks up
    against."""
    token = secrets.token_urlsafe(32)
    raw_secret = f"{KEY_PREFIX}_{token}"
    prefix = raw_secret[: len(KEY_PREFIX) + 9]  # "isk_" + 8 chars — enough to tell keys apart
    key_hash = hash_secret(raw_secret)
    return raw_secret, prefix, key_hash


def hash_secret(raw_secret):
    return hashlib.sha256(raw_secret.encode("utf-8")).hexdigest()
