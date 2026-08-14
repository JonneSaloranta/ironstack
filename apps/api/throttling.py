from rest_framework.throttling import SimpleRateThrottle


class _TierRateThrottle(SimpleRateThrottle):
    """Base for both throttle classes below — computes its actual rate
    fresh, per request, from the authenticated key's own
    `apps.api.models.RateLimitTier` rather than a static
    settings.py-configured one: an admin editing a tier's numbers in
    Django admin takes effect on every key on that tier immediately,
    with no redeploy, since nothing here is ever cached beyond a single
    request's own throttle-window lookup.

    `DEFAULT_THROTTLE_RATES` in `config.settings.base.REST_FRAMEWORK`
    still needs *some* value per `scope`, purely to satisfy
    `SimpleRateThrottle.__init__`'s constructor — `get_cache_key` below
    always overwrites `self.num_requests`/`self.duration` with the real,
    per-key numbers before `allow_request` ever reads them.
    """

    scope = None  # set by each subclass
    rate_field = None  # RateLimitTier field name to read the limit from
    duration_seconds = None

    def get_cache_key(self, request, view):
        api_key = getattr(request, "api_key", None)
        if api_key is None:
            # No authenticated key -> HasContextPermission rejects the
            # request outright; nothing here needs to also throttle it.
            return None

        self.num_requests = getattr(api_key.tier, self.rate_field)
        self.duration = self.duration_seconds
        return self.cache_format % {"scope": self.scope, "ident": api_key.pk}


class ApiKeyMinuteThrottle(_TierRateThrottle):
    scope = "api_key_minute"
    rate_field = "requests_per_minute"
    duration_seconds = 60


class ApiKeyDayThrottle(_TierRateThrottle):
    scope = "api_key_day"
    rate_field = "requests_per_day"
    duration_seconds = 60 * 60 * 24
