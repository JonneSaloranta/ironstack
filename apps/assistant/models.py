"""The AI assistant's data — see docs/ASSISTANT.md.

Nothing here ever touches another app's rows directly: the assistant
reads through each app's own services (apps.assistant.tools) and only
*proposes* changes (AssistantProposal). A proposal turns into a real
DietPlan/Program only when its owner presses "Create" — through the same
service functions an import or the diet builder already uses
(apps.assistant.proposals) — so CLAUDE.md's "automation must never take
control away from the user" holds by construction, and nothing the
assistant does can reach completed workout history at all.
"""

from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.accounts.models import EncryptedTextField
from apps.core.models import TimeStampedModel


class SharedKeyAccess(models.TextChoices):
    NOBODY = "nobody", _("Nobody — users must bring their own key")
    STAFF = "staff", _("Staff only")
    SELECTED = "selected", _("Selected users")
    EVERYONE = "everyone", _("Everyone")


class AssistantSettings(models.Model):
    """Singleton (always pk=1 — see `load()`), same pattern as
    apps.core.models.BackupSettings: the knobs an operator turns without
    a redeploy. The provider and the shared key itself stay in the
    environment (config.settings.base, ASSISTANT_*) — a secret doesn't
    belong in a database row an admin page can display."""

    enabled = models.BooleanField(
        default=True,
        help_text=_("Turns the assistant off for everyone, including users' own keys."),
    )
    shared_key_access = models.CharField(
        max_length=10,
        choices=SharedKeyAccess.choices,
        default=SharedKeyAccess.STAFF,
        help_text=_(
            "Who may use this instance's own provider (ASSISTANT_API_KEY, or the "
            "Ollama server). Its usage is billed to you, the operator."
        ),
    )
    allowed_users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="+",
        help_text=_("Used when access is set to “Selected users”."),
    )
    daily_token_limit = models.PositiveIntegerField(
        default=200_000,
        help_text=_(
            "Tokens (input + output) one user may spend per day on the shared provider. "
            "0 means no limit. Users' own keys are never limited."
        ),
    )

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        pass  # singleton — deleting it would just silently recreate defaults on next load()

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self):
        return "AI assistant settings"


class OwnKeyModel(models.TextChoices):
    """The Claude models a user may pick for their own API key (they pay
    for it, so the choice is theirs). Blank — the default — follows the
    instance's own ASSISTANT_MODEL. Kept to current models that support
    everything apps.assistant.providers sends; prices are per million
    tokens, input/output."""

    OPUS = "claude-opus-5-5", _("Claude Opus 5.5 — best plans ($4 / $20)")
    SONNET = "claude-sonnet-5-5", _("Claude Sonnet 5.5 — good and half the price ($2 / $10)")
    HAIKU = "claude-haiku-4-5", _("Claude Haiku 4.5 — fastest and cheapest, weaker plans ($1 / $5)")
    FABLE = "claude-fable-5-1", _("Claude Fable 5.1 — most capable, most expensive ($10 / $50)")


class Effort(models.TextChoices):
    """output_config.effort — how much the model thinks before it answers.
    Every OwnKeyModel except Haiku takes all five (Haiku takes none, and
    apps.assistant.providers simply doesn't send it there)."""

    LOW = "low", _("Low — quickest and cheapest; fine for everyday questions")
    MEDIUM = "medium", _("Medium — balanced")
    HIGH = "high", _("High — more thorough plans, more tokens")
    XHIGH = "xhigh", _("Extra high — for demanding planning")
    MAX = "max", _("Maximum — the most thorough and the most expensive")


class AssistantPreference(TimeStampedModel):
    """One user's own assistant settings. `consented_at` is the explicit
    opt-in: until a user has read what gets sent where and switched the
    assistant on, none of their data is ever sent to a model."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, related_name="assistant_preference", on_delete=models.CASCADE
    )
    consented_at = models.DateTimeField(null=True, blank=True)
    # The user's own Anthropic API key — always encrypted at rest, see
    # config.settings.base's ASSISTANT_ENCRYPTION_KEY. Never rendered
    # back; the UI only ever shows `api_key_hint`.
    api_key = EncryptedTextField(blank=True, default="", key_setting="ASSISTANT_ENCRYPTION_KEY")
    api_key_hint = models.CharField(max_length=12, blank=True)
    # Only used with the user's own key; blank follows the instance default.
    # A conversation keeps the model it started with (Conversation.model).
    own_key_model = models.CharField(max_length=50, choices=OwnKeyModel.choices, blank=True)
    # Likewise own-key only, blank = ASSISTANT_EFFORT. Unlike the model it
    # isn't pinned per conversation: it applies from the next reply on.
    own_key_effort = models.CharField(max_length=10, choices=Effort.choices, blank=True)

    def __str__(self):
        return f"{self.user.username}: assistant preference"

    @property
    def enabled(self):
        return self.consented_at is not None

    @property
    def has_own_key(self):
        return bool(self.api_key)


class KeySource(models.TextChoices):
    SHARED = "shared", _("This instance's provider")
    OWN = "own", _("Your own API key")


class Conversation(TimeStampedModel):
    """One chat. `provider`/`model`/`key_source` are fixed when it
    starts: the stored history (AssistantMessage.raw) is in that
    provider's own wire format and must be replayed to it unchanged, so a
    conversation never silently switches provider halfway through."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="assistant_conversations", on_delete=models.CASCADE
    )
    title = models.CharField(max_length=120)
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=100)
    key_source = models.CharField(max_length=10, choices=KeySource.choices)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"{self.user.username}: {self.title}"


class MessageRole(models.TextChoices):
    USER = "user", _("You")
    ASSISTANT = "assistant", _("Assistant")


class MessageStatus(models.TextChoices):
    PENDING = "pending", _("Waiting")
    RUNNING = "running", _("Writing")
    DONE = "done", _("Done")
    ERROR = "error", _("Failed")


class AssistantMessage(TimeStampedModel):
    """One visible chat bubble.

    `text` is what the page shows. `raw` is the list of provider-format
    API messages this bubble contributed to the conversation history —
    for a user bubble its one user message; for an assistant bubble every
    assistant turn and tool-result message of its tool loop, exactly as
    exchanged. The next request replays every bubble's `raw` in order,
    append-only (rows are never edited once DONE), which is what keeps
    the model's own thinking blocks valid across turns. A failed reply
    keeps `raw` empty, so a half-finished tool loop never ends up in the
    history.

    An assistant bubble is created PENDING; the assistant worker (or the
    request itself with ASSISTANT_RUN_INLINE) claims it, sets RUNNING and
    keeps `text` updated while the reply streams in — the page polls it.
    """

    conversation = models.ForeignKey(
        Conversation, related_name="messages", on_delete=models.CASCADE
    )
    role = models.CharField(max_length=10, choices=MessageRole.choices)
    status = models.CharField(
        max_length=10, choices=MessageStatus.choices, default=MessageStatus.DONE, db_index=True
    )
    text = models.TextField(blank=True)
    raw = models.JSONField(default=list, blank=True)
    # A short, user-facing note on what the assistant is doing right now
    # ("Looking at your workouts…") while RUNNING.
    activity = models.CharField(max_length=200, blank=True)
    error = models.TextField(blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.conversation_id}/{self.role}: {self.text[:40]}"

    @property
    def is_in_progress(self):
        return self.status in (MessageStatus.PENDING, MessageStatus.RUNNING)

    @property
    def waiting_long(self):
        """Still unclaimed well after it was sent — most likely no
        assistant worker is running (docs/ASSISTANT.md)."""
        return (
            self.status == MessageStatus.PENDING
            and timezone.now() - self.created_at > timedelta(seconds=20)
        )


class ProposalKind(models.TextChoices):
    DIET_PLAN = "diet_plan", _("Diet plan")
    PROGRAM = "program", _("Workout program")


class ProposalStatus(models.TextChoices):
    PENDING = "pending", _("Waiting for you")
    ACCEPTED = "accepted", _("Created")
    DISMISSED = "dismissed", _("Dismissed")


class AssistantProposal(TimeStampedModel):
    """Something the assistant suggests creating. `payload` is already
    validated and normalized (apps.assistant.proposals) when the row is
    written, and validated again on accept — the user's library can have
    changed in between (a food deleted, say). Accepting creates a brand
    new, inactive DietPlan/Program the user then reviews and edits like
    any other; the proposal itself is kept as a record either way."""

    message = models.ForeignKey(
        AssistantMessage, related_name="proposals", on_delete=models.CASCADE
    )
    kind = models.CharField(max_length=20, choices=ProposalKind.choices)
    title = models.CharField(max_length=200)
    payload = models.JSONField()
    status = models.CharField(
        max_length=10, choices=ProposalStatus.choices, default=ProposalStatus.PENDING
    )
    # The DietPlan/Program created on accept — a plain id, not a foreign
    # key: the user may delete that plan later, and this record of "the
    # assistant suggested it and you created it" should outlive it.
    created_object_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.kind}: {self.title}"


class UsageRecord(models.Model):
    """Tokens one user spent on one day, per key source — what the daily
    limit on the shared provider is checked against, and what the staff
    settings page summarizes."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="assistant_usage", on_delete=models.CASCADE
    )
    date = models.DateField()
    key_source = models.CharField(max_length=10, choices=KeySource.choices)
    requests = models.PositiveIntegerField(default=0)
    input_tokens = models.PositiveBigIntegerField(default=0)
    output_tokens = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ["-date"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "date", "key_source"], name="unique_assistant_usage_per_day"
            ),
        ]

    def __str__(self):
        return f"{self.user.username} {self.date} {self.key_source}"

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens
