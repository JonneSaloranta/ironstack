"""Web Push subscribe/unsubscribe — the two endpoints
static/js/push-subscribe.js calls after the browser's own
`pushManager.subscribe()`/`.unsubscribe()`. JSON in, JSON out: the
caller is client JS via fetch(), not a form submit, so there's no page
to redirect back to (docs/SECURITY.md "Web Push notifications")."""

import json

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseBadRequest, JsonResponse
from django.views import View

from apps.core.models import PushSubscription


class PushSubscribeView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            data = json.loads(request.body)
            endpoint = data["endpoint"]
            keys = data["keys"]
            p256dh = keys["p256dh"]
            auth = keys["auth"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return HttpResponseBadRequest("Malformed subscription.")
        # update_or_create, not create: a browser can legitimately call
        # pushManager.subscribe() again for a subscription it already
        # holds (e.g. after its own key rotation) — a bare create()
        # would hit endpoint's unique constraint and 500 on the second
        # call instead of just refreshing the row.
        PushSubscription.objects.update_or_create(
            endpoint=endpoint,
            defaults={"user": request.user, "p256dh_key": p256dh, "auth_key": auth},
        )
        return JsonResponse({"status": "subscribed"})


class PushUnsubscribeView(LoginRequiredMixin, View):
    def post(self, request):
        try:
            endpoint = json.loads(request.body)["endpoint"]
        except (json.JSONDecodeError, KeyError, TypeError):
            return HttpResponseBadRequest("Malformed request.")
        # Scoped to the requesting user — same "a request against
        # someone else's row just does nothing / 404s, never a 403
        # confirming it exists" shape used elsewhere in this app
        # (apps.api.viewsets.OwnedResourceViewSet's own docstring).
        PushSubscription.objects.filter(user=request.user, endpoint=endpoint).delete()
        return JsonResponse({"status": "unsubscribed"})
