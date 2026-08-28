"""Web Push notifications (docs/SECURITY.md "Web Push notifications")
— currently triggered only from apps.social.services'
send_direct_message/send_group_message, but kept generic (any
user/title/body/url) rather than message-specific, since push itself
isn't inherently a social-only concern.
"""

import json
import logging

import requests
from django.conf import settings
from pywebpush import WebPushException, webpush

from .models import PushSubscription

logger = logging.getLogger(__name__)

# Bounds worst-case latency added to whatever synchronous call site
# triggered this (today: right after a message is saved) — a slow or
# unreachable push service must never delay or break that. Internal
# reliability bound, not exposed as a setting: nothing about it is a
# self-hoster's decision to make.
_TIMEOUT_SECONDS = 5


def send_push_notification(user, title, body, url=None):
    """Sends `title`/`body` (plus an optional `url` to open on click)
    to every device `user` has enabled notifications on. Silently a
    no-op if push isn't configured (settings.PUSH_ENABLED) or the user
    has no subscriptions. Never raises — a dead or unreachable
    subscription is logged (and, if the push service confirms it's
    permanently gone, deleted) rather than propagated, since this
    always runs synchronously right after the real work (saving a
    message) it must never be allowed to delay or break."""
    if not settings.PUSH_ENABLED:
        return
    payload = json.dumps({"title": title, "body": body, "url": url})
    for subscription in PushSubscription.objects.filter(user=user):
        try:
            webpush(
                subscription_info={
                    "endpoint": subscription.endpoint,
                    "keys": {
                        "p256dh": subscription.p256dh_key,
                        "auth": subscription.auth_key,
                    },
                },
                data=payload,
                vapid_private_key=settings.VAPID_PRIVATE_KEY,
                vapid_claims={"sub": f"mailto:{settings.VAPID_ADMIN_EMAIL}"},
                timeout=_TIMEOUT_SECONDS,
            )
        except WebPushException as exc:
            # 404/410: the browser/OS has permanently discarded this
            # subscription (uninstalled, permission revoked, storage
            # cleared) — the push service itself is telling us to stop
            # trying, not a transient failure to just log and move on
            # from like anything else here.
            status = exc.response.status_code if exc.response is not None else None
            if status in (404, 410):
                subscription.delete()
            else:
                logger.warning("Push notification failed for %s: %s", user, exc)
        except requests.exceptions.RequestException as exc:
            # Connection refused/timed out/DNS failure, etc. —
            # pywebpush.webpush() doesn't wrap these into
            # WebPushException itself (confirmed by reading its
            # source: the request that can raise these isn't inside
            # any try/except of its own), so they're caught here
            # explicitly rather than assuming WebPushException alone
            # covers every failure mode.
            logger.warning("Push notification request failed for %s: %s", user, exc)
