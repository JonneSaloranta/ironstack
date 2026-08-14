"""Seed a starting set of RateLimitTier rows so a fresh install has
something to assign new API keys to immediately (apps.api.services
.default_tier() reads whichever one is flagged is_default) — see
docs/API.md "Rate limit tiers". Purely a starting point: an admin can
freely edit, add, or remove tiers from here via Django admin.
"""

from django.db import migrations

TIERS = [
    # (name, requests_per_minute, requests_per_day, is_default)
    ("Basic", 30, 2000, False),
    ("Standard", 100, 10000, True),
    ("Extended", 300, 50000, False),
]


def seed(apps, schema_editor):
    RateLimitTier = apps.get_model("api", "RateLimitTier")
    for name, per_minute, per_day, is_default in TIERS:
        RateLimitTier.objects.get_or_create(
            name=name,
            defaults={
                "requests_per_minute": per_minute,
                "requests_per_day": per_day,
                "is_default": is_default,
            },
        )


def unseed(apps, schema_editor):
    RateLimitTier = apps.get_model("api", "RateLimitTier")
    RateLimitTier.objects.filter(name__in=[name for name, *_ in TIERS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
