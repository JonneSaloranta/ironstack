"""The one place every outbound Open Food Facts request goes through
(apps.nutrition.openfoodfacts and apps.nutrition.open_prices) — see
docs/NUTRITION.md "OpenFoodFacts integration" for the policy this
implements.

OFF's usage rules (https://openfoodfacts.github.io/openfoodfacts-server/api/):
every request carries an `AppName/Version (contact)` User-Agent, and
per IP address at most 15 product reads and 10 searches per minute
(exceeding them risks an IP ban). The limits are enforced here, with
a safety margin, using Django's cache — `DatabaseCache` in this
project, so the counters are shared across gunicorn workers.
"""

import requests
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.core.version import get_version

REQUEST_TIMEOUT_SECONDS = 10
STAGING_AUTH = ("off", "off")

# OFF allows 10 searches / 15 reads a minute; stay under both.
SEARCH_LIMIT_PER_MINUTE = 8
READ_LIMIT_PER_MINUTE = 12

# After a failure (outage, 429, ban) don't retry on every page view.
FAILURE_COOLDOWN_SECONDS = 300
_FAILURE_KEY = "nutrition:off_failure"


class OffRequestError(Exception):
    """Network/HTTP failure talking to an OFF service."""


class OffRateLimited(OffRequestError):
    """Our own per-minute budget is spent, or OFF recently failed —
    no request was sent."""


def user_agent():
    """`IronStack/<version> (<contact>)`. The contact is the operator's
    own address (OpenFoodFactsSettings.contact_email, falling back to
    the OFF_CONTACT_EMAIL setting) — never hardcoded, since it's
    whoever runs this instance."""
    from .models import OpenFoodFactsSettings

    contact = OpenFoodFactsSettings.load().contact_email or getattr(
        settings, "OFF_CONTACT_EMAIL", ""
    )
    detail = contact or "self-hosted fitness tracker"
    return f"IronStack/{get_version()} ({detail})"


def _within_budget(bucket, limit):
    """Fixed one-minute window counter, shared via the cache."""
    key = f"nutrition:off_budget:{bucket}:{int(timezone.now().timestamp() // 60)}"
    cache.add(key, 0, timeout=120)
    try:
        return cache.incr(key) <= limit
    except ValueError:  # key expired between add() and incr()
        cache.set(key, 1, timeout=120)
        return True


def get(url, *, bucket, params=None):
    """A rate-limited, identified GET returning the `requests.Response`.
    `bucket` is "search" or "read". Raises OffRateLimited without
    touching the network when over budget or in a failure cooldown,
    and OffRequestError for anything else that goes wrong."""
    if cache.get(_FAILURE_KEY):
        raise OffRateLimited("recent Open Food Facts failure, backing off")
    limit = SEARCH_LIMIT_PER_MINUTE if bucket == "search" else READ_LIMIT_PER_MINUTE
    if not _within_budget(bucket, limit):
        raise OffRateLimited(f"{bucket} budget for this minute is spent")
    try:
        response = requests.get(
            url,
            params=params,
            headers={"User-Agent": user_agent()},
            # OFF's staging servers (*.openfoodfacts.net) sit behind
            # a public basic-auth login, off/off, per their API docs.
            auth=STAGING_AUTH if ".openfoodfacts.net" in url else None,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        if status == 429 or (status is not None and status >= 500) or status is None:
            cache.set(_FAILURE_KEY, True, FAILURE_COOLDOWN_SECONDS)
        raise OffRequestError(str(exc)) from exc
    return response
