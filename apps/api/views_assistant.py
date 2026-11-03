"""The AI assistant (apps.assistant) through the API - issue #37.

The same flow as the web: turn it on (the user's consent to send their
data to a model - a client must show the same explanation the web's
settings page does first), start a conversation, poll the reply while
the assistant worker writes it, and accept or dismiss what it proposes.
Every rule stays in apps.assistant.services; an AssistantError answers
400 with its message in the user's language.

The user's own Anthropic API key is set and removed on the web only: it
is a credential, and an API key shouldn't be able to replace it - the
same reasoning that keeps `email` read-only on `profile/`. Assistant
context."""

from functools import wraps

from django.shortcuts import get_object_or_404
from django.utils.translation import gettext as _
from drf_spectacular.utils import extend_schema
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assistant import proposals as proposal_services
from apps.assistant import services as assistant_services
from apps.assistant.forms import ABOUT_ME_MAX_LENGTH
from apps.assistant.models import (
    AssistantMessage,
    AssistantProposal,
    AssistantSettings,
    Conversation,
    DataScope,
    Effort,
    KeySource,
    OwnKeyModel,
)

from .models import ApiContext
from .views import _user_language

#: The web's assistant page lists the same number.
CONVERSATION_LIST_LIMIT = 50


# --------------------------------------------------------------------
# Serializers
# --------------------------------------------------------------------


class AssistantStatusSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(help_text="The user has turned the assistant on.")
    available = serializers.BooleanField(help_text="A model can be used for this user.")
    reason = serializers.CharField(help_text="Why it isn't available, when it isn't.")
    key_source = serializers.CharField(allow_null=True)
    provider = serializers.CharField(allow_null=True)
    model = serializers.CharField(allow_null=True)
    has_own_key = serializers.BooleanField()
    own_key_hint = serializers.CharField(help_text="The key's last characters, e.g. ...abcd.")
    tokens_used_today = serializers.IntegerField(allow_null=True)
    tokens_remaining = serializers.IntegerField(allow_null=True)
    daily_limit = serializers.IntegerField(allow_null=True)


_READ_FIELDS = [f"reads_{scope.value}" for scope in DataScope]


class AssistantSettingsSerializer(serializers.Serializer):
    """What the assistant may read about the user, what it should know
    about them, and - with their own key - which model and effort."""

    about_me = serializers.CharField(
        max_length=ABOUT_ME_MAX_LENGTH, allow_blank=True, required=False
    )
    own_key_model = serializers.ChoiceField(
        choices=[("", "Default"), *OwnKeyModel.choices], required=False, allow_blank=True
    )
    own_key_effort = serializers.ChoiceField(
        choices=[("", "Default"), *Effort.choices], required=False, allow_blank=True
    )

    reads_workouts = serializers.BooleanField(required=False)
    reads_body = serializers.BooleanField(required=False)
    reads_food = serializers.BooleanField(required=False)
    reads_activities = serializers.BooleanField(required=False)
    reads_stretching = serializers.BooleanField(required=False)
    reads_profile = serializers.BooleanField(required=False)


class ProposalSerializer(serializers.ModelSerializer):
    class Meta:
        model = AssistantProposal
        fields = ["id", "kind", "title", "payload", "status", "created_object_id"]


class AssistantMessageSerializer(serializers.ModelSerializer):
    """A chat bubble. An assistant reply starts `pending`, is `running`
    while it's written (`text` grows, `activity` says what it's doing)
    and ends `done` or `error`."""

    proposals = ProposalSerializer(many=True, read_only=True)

    class Meta:
        model = AssistantMessage
        fields = [
            "id", "role", "status", "text", "activity", "error", "created_at", "proposals",
        ]


class ConversationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Conversation
        fields = ["id", "title", "provider", "model", "key_source", "created_at", "updated_at"]


class ConversationDetailSerializer(ConversationSerializer):
    messages = AssistantMessageSerializer(many=True, read_only=True)

    class Meta(ConversationSerializer.Meta):
        fields = [*ConversationSerializer.Meta.fields, "messages"]


class MessageTextSerializer(serializers.Serializer):
    text = serializers.CharField(max_length=assistant_services.MAX_MESSAGE_LENGTH)


class ProposalAcceptedSerializer(serializers.Serializer):
    proposal = ProposalSerializer()
    kind = serializers.CharField(help_text="diet_plan or program.")
    created_id = serializers.IntegerField(
        help_text="The new, inactive diet plan or program - review it like any other."
    )


# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------


def assistant_errors(method):
    @wraps(method)
    def wrapper(self, request, *args, **kwargs):
        with _user_language(request.user):
            try:
                return method(self, request, *args, **kwargs)
            except assistant_services.AssistantError as error:
                return Response({"detail": str(error)}, status=status.HTTP_400_BAD_REQUEST)

    return wrapper


class AssistantView(APIView):
    api_context = ApiContext.ASSISTANT


def _status(user):
    preference = assistant_services.preference_for(user)
    access = assistant_services.access_for(user, preference)
    usage = {"tokens_used_today": None, "tokens_remaining": None, "daily_limit": None}
    if access.key_source == KeySource.SHARED:
        usage = {
            "tokens_used_today": assistant_services.tokens_used_today(user),
            "tokens_remaining": assistant_services.remaining_shared_tokens(user),
            "daily_limit": AssistantSettings.load().daily_token_limit,
        }
    return {
        "enabled": preference.enabled,
        "available": access.available,
        "reason": str(access.reason),
        "key_source": access.key_source,
        "provider": access.provider,
        "model": access.model,
        "has_own_key": preference.has_own_key,
        "own_key_hint": preference.api_key_hint,
        **usage,
    }


def _settings(user):
    preference = assistant_services.preference_for(user)
    return {
        "about_me": preference.about_me,
        "own_key_model": preference.own_key_model,
        "own_key_effort": preference.own_key_effort,
        **{name: getattr(preference, name) for name in _READ_FIELDS},
    }


def _conversation_or_404(request, pk):
    return get_object_or_404(Conversation, pk=pk, user=request.user)


# --------------------------------------------------------------------
# Status and settings
# --------------------------------------------------------------------


class AssistantStatusView(AssistantView):
    """`GET assistant/` - whether the assistant is on and usable, with
    which provider and model, and today's allowance on the instance's
    own provider (null with the user's own key)."""

    @extend_schema(responses=AssistantStatusSerializer)
    def get(self, request):
        with _user_language(request.user):
            return Response(_status(request.user))


class AssistantToggleView(AssistantView):
    """`POST assistant/enable/` - the user's consent to send their data
    to the model (show them what the web's settings page explains
    first); `POST assistant/disable/` turns it off."""

    enable = True

    @extend_schema(request=None, responses=AssistantStatusSerializer)
    def post(self, request):
        if self.enable:
            assistant_services.enable(request.user)
        else:
            assistant_services.disable(request.user)
        with _user_language(request.user):
            return Response(_status(request.user))


class AssistantSettingsView(AssistantView):
    """`GET`/`PATCH assistant/settings/` - `reads_*` (what the assistant
    may read), `about_me`, and the model and effort used with the user's
    own key ("" for the instance default)."""

    @extend_schema(responses=AssistantSettingsSerializer)
    def get(self, request):
        return Response(_settings(request.user))

    @extend_schema(request=AssistantSettingsSerializer, responses=AssistantSettingsSerializer)
    def patch(self, request):
        serializer = AssistantSettingsSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        user = request.user
        current = _settings(user)
        if "about_me" in data:
            assistant_services.set_about_me(user, data["about_me"])
        if "own_key_model" in data or "own_key_effort" in data:
            assistant_services.set_own_key_options(
                user,
                model=data.get("own_key_model", current["own_key_model"]),
                effort=data.get("own_key_effort", current["own_key_effort"]),
            )
        if any(name in data for name in _READ_FIELDS):
            assistant_services.set_data_access(
                user, {name: data.get(name, current[name]) for name in _READ_FIELDS}
            )
        return Response(_settings(user))


# --------------------------------------------------------------------
# Conversations
# --------------------------------------------------------------------


class ConversationListView(AssistantView):
    """`GET assistant/conversations/` - the latest 50, newest first.
    `POST {text}` starts one; 201 with it, its first reply `pending`
    until the assistant worker writes it - poll `assistant/messages/
    <id>/`."""

    @extend_schema(responses=ConversationSerializer(many=True))
    def get(self, request):
        conversations = Conversation.objects.filter(user=request.user)[:CONVERSATION_LIST_LIMIT]
        return Response(ConversationSerializer(conversations, many=True).data)

    @extend_schema(request=MessageTextSerializer, responses={201: ConversationDetailSerializer})
    @assistant_errors
    def post(self, request):
        serializer = MessageTextSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation = assistant_services.start_conversation(
            request.user, serializer.validated_data["text"]
        )
        return Response(
            ConversationDetailSerializer(conversation).data, status=status.HTTP_201_CREATED
        )


class ConversationDetailView(AssistantView):
    """`GET assistant/conversations/<id>/` - with every message and its
    proposals. `DELETE` removes it."""

    @extend_schema(responses=ConversationDetailSerializer)
    def get(self, request, pk):
        return Response(ConversationDetailSerializer(_conversation_or_404(request, pk)).data)

    @extend_schema(responses={204: None})
    def delete(self, request, pk):
        _conversation_or_404(request, pk).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class ConversationMessageView(AssistantView):
    """`POST assistant/conversations/<id>/messages/ {text}` - sends the
    next message; 201 with the assistant's reply bubble, `pending` until
    it's written. A 400 while the previous reply is still being written,
    or once today's allowance is used."""

    @extend_schema(request=MessageTextSerializer, responses={201: AssistantMessageSerializer})
    @assistant_errors
    def post(self, request, pk):
        conversation = _conversation_or_404(request, pk)
        serializer = MessageTextSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        reply = assistant_services.send_message(conversation, serializer.validated_data["text"])
        return Response(AssistantMessageSerializer(reply).data, status=status.HTTP_201_CREATED)


class AssistantMessageView(AssistantView):
    """`GET assistant/messages/<id>/` - one message, to poll a reply while
    it's written (the web polls every couple of seconds)."""

    @extend_schema(responses=AssistantMessageSerializer)
    def get(self, request, pk):
        message = get_object_or_404(
            AssistantMessage, pk=pk, conversation__user=request.user
        )
        return Response(AssistantMessageSerializer(message).data)


# --------------------------------------------------------------------
# Proposals
# --------------------------------------------------------------------


def _proposal_or_404(request, pk):
    return get_object_or_404(
        AssistantProposal.objects.select_related("message__conversation"),
        pk=pk,
        message__conversation__user=request.user,
    )


class ProposalAcceptView(AssistantView):
    """`POST assistant/proposals/<id>/accept/` - creates the proposed diet
    plan or program, inactive, for the user to review (201). 409 with
    the reason when it can't be created as proposed anymore (a food
    deleted since, say)."""

    @extend_schema(request=None, responses={201: ProposalAcceptedSerializer})
    def post(self, request, pk):
        proposal = _proposal_or_404(request, pk)
        with _user_language(request.user):
            try:
                created = proposal_services.accept(proposal)
            except proposal_services.ProposalError as error:
                return Response(
                    {
                        "detail": _("This suggestion can't be created anymore: %(error)s")
                        % {"error": error}
                    },
                    status=status.HTTP_409_CONFLICT,
                )
        proposal.refresh_from_db()
        return Response(
            {
                "proposal": ProposalSerializer(proposal).data,
                "kind": proposal.kind,
                "created_id": created.pk,
            },
            status=status.HTTP_201_CREATED,
        )


class ProposalDismissView(AssistantView):
    """`POST assistant/proposals/<id>/dismiss/` - kept as a record."""

    @extend_schema(request=None, responses=ProposalSerializer)
    def post(self, request, pk):
        proposal = _proposal_or_404(request, pk)
        proposal_services.dismiss(proposal)
        proposal.refresh_from_db()
        return Response(ProposalSerializer(proposal).data)

