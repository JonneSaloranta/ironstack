"""Guided tours (apps.tutorials) through the API - issue #37.

A tour is written for a web page: each step points at an element by its
`data-tour` anchor. A client with its own screens can still show the
texts, and the completion state is shared with the web, so a tour
finished in one isn't offered again in the other. Profile context, as
on the web (profile -> Tutorials)."""

from django.utils import translation
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.tutorials import services as tutorial_services
from apps.tutorials.tours import TOURS

from .models import ApiContext
from .serializers import display_language


class TutorialStepSerializer(serializers.Serializer):
    anchor = serializers.CharField(allow_null=True)
    title = serializers.CharField()
    body = serializers.CharField()
    optional = serializers.BooleanField()


class TutorialSerializer(serializers.Serializer):
    key = serializers.CharField()
    section = serializers.CharField()
    title = serializers.CharField()
    completed = serializers.BooleanField()
    web_url = serializers.CharField(
        allow_null=True, help_text="The web page the tour runs on; null when there's none yet."
    )
    steps = TutorialStepSerializer(many=True)


def _tour_or_404(key):
    tour = tutorial_services.tour_by_key(key)
    if tour is None:
        from django.http import Http404

        raise Http404
    return tour


class TutorialListView(APIView):
    """`GET tutorials/` - every tour with its steps, in the request's or
    the user's language, and whether the user has finished it.
    Automatic tours are switched with `tutorials_enabled` on
    `profile/`."""

    api_context = ApiContext.PROFILE

    @extend_schema(responses=TutorialSerializer(many=True))
    def get(self, request):
        done = tutorial_services.completed_keys(request.user)
        with translation.override(display_language(request)):
            data = TutorialSerializer(
                [
                    {
                        "key": tour.key,
                        "section": str(tour.section),
                        "title": str(tour.title),
                        "completed": tour.key in done,
                        "web_url": tour.url_for(request.user),
                        "steps": [
                            {
                                "anchor": step.anchor,
                                "title": str(step.title),
                                "body": str(step.body),
                                "optional": step.optional,
                            }
                            for step in tour.steps
                        ],
                    }
                    for tour in TOURS
                ],
                many=True,
            ).data
        return Response(data)


class TutorialCompleteView(APIView):
    """`POST tutorials/<key>/complete/` - finished or skipped, so it
    isn't started by itself again (also on the web)."""

    api_context = ApiContext.PROFILE

    @extend_schema(request=None, responses={204: None})
    def post(self, request, key):
        tutorial_services.mark_completed(request.user, _tour_or_404(key).key)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TutorialResetView(APIView):
    """`POST tutorials/<key>/reset/` - starts again next time ("Show
    again")."""

    api_context = ApiContext.PROFILE

    @extend_schema(request=None, responses={204: None})
    def post(self, request, key):
        tutorial_services.reset(request.user, _tour_or_404(key).key)
        return Response(status=status.HTTP_204_NO_CONTENT)


class TutorialResetAllView(APIView):
    """`POST tutorials/reset/` - every tour starts again next time."""

    api_context = ApiContext.PROFILE

    @extend_schema(request=None, responses={204: None}, operation_id="tutorials_reset_all")
    def post(self, request):
        tutorial_services.reset(request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
