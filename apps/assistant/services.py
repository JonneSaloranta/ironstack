"""Assistant domain logic — who may use it, with which key, within which
limits, and the tool loop that writes a reply. See docs/ASSISTANT.md.

Views only call `access_for`, `start_conversation`/`send_message` and
render; the reply itself is written by `process_message`, run by the
assistant worker (or inline with ASSISTANT_RUN_INLINE).
"""

import logging
import time
from dataclasses import dataclass

from django.conf import settings
from django.db import transaction
from django.db.models import F, Sum
from django.utils import timezone, translation
from django.utils.translation import gettext as _

from . import prompts, tools
from .models import (
    AssistantMessage,
    AssistantPreference,
    AssistantSettings,
    Conversation,
    KeySource,
    MessageRole,
    MessageStatus,
    SharedKeyAccess,
    UsageRecord,
)
from .providers import (
    ANTHROPIC,
    OLLAMA,
    AnthropicProvider,
    OllamaProvider,
    ProviderError,
    ToolResult,
)

logger = logging.getLogger(__name__)

# Model turns per reply. Each turn can call several tools at once, so
# this is plenty for "look at three things, then propose a plan" while
# still bounding what one runaway reply can cost.
MAX_TOOL_ROUNDS = 12
# A conversation's whole history is re-sent with every reply; past this
# the user is asked to start a new one rather than paying for an ever
# longer prompt.
MAX_USER_MESSAGES_PER_CONVERSATION = 40
MAX_MESSAGE_LENGTH = 4000
# How often a streaming reply's partial text is written to the database
# for the polling page.
_TEXT_FLUSH_SECONDS = 0.5
# A RUNNING reply untouched this long belongs to a worker that died.
STALE_AFTER = timezone.timedelta(minutes=10)
DEFAULT_CLAUDE_MODEL = "claude-opus-5-5"


class AssistantError(Exception):
    """Something the user tried that isn't possible right now — the
    message is translated and shown as-is."""


@dataclass
class Access:
    available: bool
    reason: str = ""
    key_source: str | None = None
    provider: str | None = None
    model: str | None = None


def preference_for(user):
    """The user's preference row, or an unsaved default — reading the
    assistant pages never writes anything."""
    return AssistantPreference.objects.filter(user=user).first() or AssistantPreference(user=user)


def shared_provider_configured():
    if settings.ASSISTANT_PROVIDER == OLLAMA:
        return bool(settings.ASSISTANT_OLLAMA_URL and settings.ASSISTANT_OLLAMA_MODEL)
    return settings.ASSISTANT_PROVIDER == ANTHROPIC and bool(settings.ASSISTANT_API_KEY)


def may_use_shared(user, site_settings):
    access = site_settings.shared_key_access
    if access == SharedKeyAccess.EVERYONE:
        return True
    if access == SharedKeyAccess.STAFF:
        return user.is_staff
    if access == SharedKeyAccess.SELECTED:
        return user.is_staff or site_settings.allowed_users.filter(pk=user.pk).exists()
    return False


def _claude_model():
    """The Claude model for a user's own key: the configured one when the
    instance itself runs on Claude, otherwise the default."""
    if settings.ASSISTANT_PROVIDER == ANTHROPIC and settings.ASSISTANT_MODEL:
        return settings.ASSISTANT_MODEL
    return DEFAULT_CLAUDE_MODEL


def access_for(user, preference=None):
    """Whether `user` can use the assistant at all, and with what. Their
    own key wins over the shared provider: it's what they set it for."""
    site_settings = AssistantSettings.load()
    if not site_settings.enabled:
        return Access(False, _("The administrator has turned the AI assistant off."))
    preference = preference or preference_for(user)
    if preference.has_own_key:
        return Access(True, key_source=KeySource.OWN, provider=ANTHROPIC, model=_claude_model())
    if shared_provider_configured() and may_use_shared(user, site_settings):
        model = (
            settings.ASSISTANT_OLLAMA_MODEL
            if settings.ASSISTANT_PROVIDER == OLLAMA
            else settings.ASSISTANT_MODEL
        )
        return Access(
            True, key_source=KeySource.SHARED, provider=settings.ASSISTANT_PROVIDER, model=model
        )
    return Access(
        False,
        _(
            "This instance doesn't provide an AI model for your account. You can use the "
            "assistant with your own Anthropic API key."
        ),
    )


def is_available(user):
    """For entry points elsewhere in the app (a link on the diet plan
    list): only offered to someone who could actually use it."""
    preference = preference_for(user)
    return preference.enabled and access_for(user, preference).available


# --- Preferences ----------------------------------------------------------


def enable(user):
    preference, _created = AssistantPreference.objects.get_or_create(user=user)
    if preference.consented_at is None:
        preference.consented_at = timezone.now()
        preference.save(update_fields=["consented_at", "updated_at"])
    return preference


def disable(user):
    AssistantPreference.objects.filter(user=user).update(consented_at=None)


def set_own_key(user, api_key):
    preference, _created = AssistantPreference.objects.get_or_create(user=user)
    preference.api_key = api_key
    preference.api_key_hint = f"…{api_key[-4:]}"
    preference.save(update_fields=["api_key", "api_key_hint", "updated_at"])
    return preference


def remove_own_key(user):
    AssistantPreference.objects.filter(user=user).update(api_key="", api_key_hint="")


# --- Usage ----------------------------------------------------------------


def tokens_used_today(user, key_source=KeySource.SHARED):
    totals = UsageRecord.objects.filter(
        user=user, date=timezone.localdate(), key_source=key_source
    ).aggregate(input=Sum("input_tokens"), output=Sum("output_tokens"))
    return (totals["input"] or 0) + (totals["output"] or 0)


def remaining_shared_tokens(user):
    """None when there's no limit."""
    limit = AssistantSettings.load().daily_token_limit
    if not limit:
        return None
    return max(0, limit - tokens_used_today(user))


def record_usage(user, key_source, input_tokens, output_tokens):
    record, _created = UsageRecord.objects.get_or_create(
        user=user, date=timezone.localdate(), key_source=key_source
    )
    UsageRecord.objects.filter(pk=record.pk).update(
        requests=F("requests") + 1,
        input_tokens=F("input_tokens") + input_tokens,
        output_tokens=F("output_tokens") + output_tokens,
    )


def usage_summary(days=30):
    """Per-user token totals over the last `days` days, for the staff
    settings page — biggest spenders first."""
    since = timezone.localdate() - timezone.timedelta(days=days - 1)
    return (
        UsageRecord.objects.filter(date__gte=since)
        .values("user__username", "key_source")
        .annotate(requests=Sum("requests"), input=Sum("input_tokens"), output=Sum("output_tokens"))
        .order_by("-input", "-output")
    )


# --- Conversations --------------------------------------------------------


def _check_can_send(user, conversation=None):
    preference = preference_for(user)
    if not preference.enabled:
        raise AssistantError(_("Turn the assistant on in its settings first."))
    access = access_for(user, preference)
    if not access.available:
        raise AssistantError(access.reason)
    if AssistantMessage.objects.filter(
        conversation__user=user,
        status__in=[MessageStatus.PENDING, MessageStatus.RUNNING],
    ).exists():
        raise AssistantError(_("Wait for the assistant to finish its current reply."))
    key_source = conversation.key_source if conversation else access.key_source
    if key_source == KeySource.SHARED and remaining_shared_tokens(user) == 0:
        raise AssistantError(_("You've used today's assistant allowance. It resets tomorrow."))
    if conversation is not None:
        if conversation.key_source == KeySource.OWN and not preference.has_own_key:
            raise AssistantError(
                _(
                    "This conversation used your own API key, which you've removed. "
                    "Start a new conversation."
                )
            )
        if conversation.key_source == KeySource.SHARED and access.key_source != KeySource.SHARED:
            if not (
                shared_provider_configured() and may_use_shared(user, AssistantSettings.load())
            ):
                raise AssistantError(
                    _("This conversation can't be continued anymore. Start a new conversation.")
                )
        user_messages = conversation.messages.filter(role=MessageRole.USER).count()
        if user_messages >= MAX_USER_MESSAGES_PER_CONVERSATION:
            raise AssistantError(
                _("This conversation is getting long. Start a new one to keep replies quick.")
            )
    return access


def _provider_for(conversation):
    if conversation.provider == OLLAMA:
        return OllamaProvider(base_url=settings.ASSISTANT_OLLAMA_URL, model=conversation.model)
    if conversation.key_source == KeySource.OWN:
        api_key = preference_for(conversation.user).api_key
    else:
        api_key = settings.ASSISTANT_API_KEY
    if not api_key:
        raise ProviderError(_("No API key is available for this conversation."))
    return AnthropicProvider(
        api_key=api_key,
        model=conversation.model,
        effort=settings.ASSISTANT_EFFORT,
        refusal_fallback=settings.ASSISTANT_REFUSAL_FALLBACK,
    )


def _clean_text(text):
    text = (text or "").strip()
    if not text:
        raise AssistantError(_("Write a message first."))
    if len(text) > MAX_MESSAGE_LENGTH:
        raise AssistantError(_("That message is too long."))
    return text


def _queue_reply(conversation, text, *, first):
    provider = _provider_for_wire_format(conversation)
    content = f"{prompts.conversation_context(conversation.user)}\n\n{text}" if first else text
    AssistantMessage.objects.create(
        conversation=conversation,
        role=MessageRole.USER,
        text=text,
        raw=[provider.user_message(content)],
    )
    reply = AssistantMessage.objects.create(
        conversation=conversation, role=MessageRole.ASSISTANT, status=MessageStatus.PENDING
    )
    Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())
    if settings.ASSISTANT_RUN_INLINE:
        process_message(reply.pk)
        reply.refresh_from_db()
    return reply


def _provider_for_wire_format(conversation):
    """A provider object only for building messages — no key needed."""
    if conversation.provider == OLLAMA:
        return OllamaProvider(base_url="", model=conversation.model)
    return AnthropicProvider(api_key="", model=conversation.model)


def start_conversation(user, text):
    text = _clean_text(text)
    access = _check_can_send(user)
    conversation = Conversation.objects.create(
        user=user,
        title=text[:80] + ("…" if len(text) > 80 else ""),
        provider=access.provider,
        model=access.model,
        key_source=access.key_source,
    )
    _queue_reply(conversation, text, first=True)
    return conversation


def send_message(conversation, text):
    text = _clean_text(text)
    _check_can_send(conversation.user, conversation)
    return _queue_reply(conversation, text, first=False)


# --- Writing a reply ------------------------------------------------------


def _claim(message_id):
    with transaction.atomic():
        message = (
            AssistantMessage.objects.select_for_update(skip_locked=True)
            .filter(pk=message_id, status=MessageStatus.PENDING)
            .select_related("conversation__user")
            .first()
        )
        if message is None:
            return None
        message.status = MessageStatus.RUNNING
        message.save(update_fields=["status", "updated_at"])
        return message


class _ReplyWriter:
    """Keeps the visible text of a reply in step with the stream, writing
    it to the database at most every _TEXT_FLUSH_SECONDS."""

    def __init__(self, message):
        self.message = message
        self.finished_parts = []
        self.current = ""
        self.last_flush = 0.0

    def text(self):
        return "\n\n".join(part for part in [*self.finished_parts, self.current] if part)

    def on_text(self, text):
        self.current = text
        now = time.monotonic()
        if now - self.last_flush >= _TEXT_FLUSH_SECONDS:
            self.flush()
            self.last_flush = now

    def finish_turn(self, text):
        if text:
            self.finished_parts.append(text)
        self.current = ""

    def set_activity(self, activity):
        self.message.activity = activity
        self.flush()

    def flush(self):
        self.message.text = self.text()
        AssistantMessage.objects.filter(pk=self.message.pk).update(
            text=self.message.text, activity=self.message.activity, updated_at=timezone.now()
        )


def process_message(message_id):
    """Writes the reply for one PENDING assistant message: the tool loop,
    usage accounting, and the final DONE/ERROR state. Safe to call from
    several workers at once — only one claims a given message."""
    message = _claim(message_id)
    if message is None:
        return
    conversation = message.conversation
    user = conversation.user
    with translation.override(user.language), timezone.override(user.timezone):
        _write_reply(message, conversation, user)


def _write_reply(message, conversation, user):
    writer = _ReplyWriter(message)
    raw = []
    notes = []
    try:
        provider = _provider_for(conversation)
        history = []
        for earlier in conversation.messages.filter(status=MessageStatus.DONE).exclude(
            pk=message.pk
        ):
            history.extend(earlier.raw)
        context = tools.ToolContext(user, message)
        definitions = tools.definitions()
        for _round in range(MAX_TOOL_ROUNDS):
            writer.set_activity(_("Thinking…"))
            result = provider.run_turn(
                system=prompts.SYSTEM_PROMPT,
                tools=definitions,
                history=history + raw,
                on_text=writer.on_text,
            )
            record_usage(user, conversation.key_source, result.input_tokens, result.output_tokens)
            AssistantMessage.objects.filter(pk=message.pk).update(
                input_tokens=F("input_tokens") + result.input_tokens,
                output_tokens=F("output_tokens") + result.output_tokens,
            )
            if result.stop_reason == "refusal":
                writer.finish_turn("")
                notes.append(_("The assistant can't help with that request."))
                break
            if result.stop_reason == "max_tokens" and result.tool_calls:
                # A cut-off tool call can't be answered; leave the whole
                # turn out of the history.
                writer.finish_turn(result.text)
                notes.append(_("The reply was cut off. Try asking for something smaller."))
                break
            raw.extend(result.raw)
            writer.finish_turn(result.text)
            if result.stop_reason != "tool_use":
                if result.stop_reason == "max_tokens":
                    notes.append(_("The reply was cut off."))
                break
            results = []
            for call in result.tool_calls:
                writer.set_activity(tools.activity_for(call.name))
                content, is_error = tools.run(context, call.name, call.input)
                results.append(ToolResult(call=call, content=content, is_error=is_error))
            raw.extend(provider.tool_results_message(results))
        else:
            notes.append(_("The assistant stopped after too many steps."))
    except ProviderError as exc:
        _fail(message, writer, str(exc))
        return
    except Exception:
        logger.exception("Assistant reply %s failed", message.pk)
        _fail(message, writer, _("Something went wrong while writing the reply."))
        return

    text = writer.text()
    if notes:
        text = "\n\n".join(part for part in [text, *notes] if part)
    AssistantMessage.objects.filter(pk=message.pk).update(
        status=MessageStatus.DONE,
        text=text,
        raw=raw,
        activity="",
        updated_at=timezone.now(),
    )
    Conversation.objects.filter(pk=conversation.pk).update(updated_at=timezone.now())


def _fail(message, writer, error):
    AssistantMessage.objects.filter(pk=message.pk).update(
        status=MessageStatus.ERROR,
        text=writer.text(),
        raw=[],
        activity="",
        error=error,
        updated_at=timezone.now(),
    )


def fail_stale_messages():
    """Marks replies a crashed worker left RUNNING as failed, so their
    users can send again."""
    return AssistantMessage.objects.filter(
        status=MessageStatus.RUNNING, updated_at__lt=timezone.now() - STALE_AFTER
    ).update(
        status=MessageStatus.ERROR,
        activity="",
        raw=[],
        error=_("The reply was interrupted. Please try again."),
    )


def pending_message_ids(limit=10):
    return list(
        AssistantMessage.objects.filter(status=MessageStatus.PENDING)
        .order_by("created_at")
        .values_list("pk", flat=True)[:limit]
    )
