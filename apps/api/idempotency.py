"""`Idempotency-Key` support for create requests (issue #22).

A client on a bad connection retries a write until it gets an answer.
When the first attempt did reach the server and only its response was
lost, the retry would create the same set (or session) a second time.
With an `Idempotency-Key` header - one random value per queued write,
sent unchanged with every retry - the first response is stored and a
repeat gets that same response back instead of a new row. Without the
header a request behaves exactly as before. See docs/API.md "Retrying
writes".
"""

import hashlib
import json
from datetime import timedelta

from django.core.serializers.json import DjangoJSONEncoder
from django.db import IntegrityError, transaction
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter
from rest_framework import status
from rest_framework.response import Response

from .models import IdempotencyKey

HEADER = "Idempotency-Key"
#: How long a key is remembered - far longer than any client's retry queue.
KEY_LIFETIME = timedelta(hours=24)
MAX_KEY_LENGTH = 255

#: For `extend_schema(parameters=...)` on each create that supports it.
SCHEMA_PARAMETER = OpenApiParameter(
    HEADER,
    OpenApiTypes.STR,
    location=OpenApiParameter.HEADER,
    required=False,
    description=(
        "Optional, e.g. a UUID per queued write. A retry with the same key gets the "
        "first response back instead of creating another row. Remembered for 24 hours."
    ),
)


def _request_hash(request):
    payload = json.dumps(
        [request.method, request.path, request.data],
        sort_keys=True,
        cls=DjangoJSONEncoder,
        default=str,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _replay(stored, request_hash):
    if stored.request_hash != request_hash:
        return Response(
            {"detail": f"This {HEADER} was already used for a different request."},
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )
    return Response(
        stored.response_body,
        status=stored.response_status,
        headers={"Idempotent-Replayed": "true"},
    )


class IdempotentCreateMixin:
    """For a DRF viewset whose `create` should honour `Idempotency-Key`.
    Only a successful (2xx) response is stored: a failed attempt changed
    nothing, so retrying it with the same key simply runs it again."""

    def create(self, request, *args, **kwargs):
        key = request.headers.get(HEADER)
        if key is None:
            return super().create(request, *args, **kwargs)
        if not key.strip() or len(key) > MAX_KEY_LENGTH:
            return Response(
                {"detail": f"{HEADER} must be 1-{MAX_KEY_LENGTH} characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = request.user
        request_hash = _request_hash(request)
        cutoff = timezone.now() - KEY_LIFETIME
        live = IdempotencyKey.objects.filter(user=user, key=key, created_at__gte=cutoff)
        try:
            with transaction.atomic():
                stored = live.first()
                if stored is not None:
                    return _replay(stored, request_hash)
                # Clears this key if it expired, and the user's other old ones.
                IdempotencyKey.objects.filter(user=user, created_at__lt=cutoff).delete()
                response = super().create(request, *args, **kwargs)
                if status.is_success(response.status_code):
                    IdempotencyKey.objects.create(
                        user=user,
                        key=key,
                        request_hash=request_hash,
                        response_status=response.status_code,
                        response_body=response.data,
                    )
                return response
        except IntegrityError:
            # The same key arrived twice at once and the other request
            # stored first - its row creation was rolled back with ours.
            return _replay(live.get(), request_hash)
