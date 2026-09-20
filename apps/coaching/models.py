"""Personal-trainer coaching — a directed, consent-gated relationship
between two users, letting a coach (a user with `User.
is_personal_trainer` on) share gym programs/diet plans with, and view
the training/body-weight data of, whichever coachees have actually
accepted them. Every rule about who's allowed to do what lives in
apps.coaching.services instead (CLAUDE.md: "keep business/domain logic
out of views" applies just as much to models).

Deliberately not modeled on apps.social.Friendship's symmetric
user_low/user_high shape — a coach and a coachee are never
interchangeable, so both models here use plain, always-meaningful
`coach`/`coachee` fields instead.
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.core.models import TimeStampedModel


class CoachingRequestStatus(models.TextChoices):
    PENDING = "pending", _("Pending")
    ACCEPTED = "accepted", _("Accepted")
    DECLINED = "declined", _("Declined")


class CoachingRequest(TimeStampedModel):
    """A coachee asking a PT to coach them — always coachee -> coach,
    never the reverse (unlike apps.social.FriendRequest, there's no
    symmetric "either side can propose" case, so no reverse-pending
    auto-accept logic like send_friend_request's is needed here).
    Kept around after a decision, same reasoning as FriendRequest's
    own docstring — apps.coaching.services enforces the actual rules
    (can't request yourself, the target must actually be a PT who's
    accepting new clients, no duplicate pending request, can't
    request someone already actively coaching you)."""

    coach = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="received_coaching_requests",
        on_delete=models.CASCADE,
    )
    coachee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        related_name="sent_coaching_requests",
        on_delete=models.CASCADE,
    )
    status = models.CharField(
        max_length=10,
        choices=CoachingRequestStatus.choices,
        default=CoachingRequestStatus.PENDING,
    )
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["coach", "coachee"], name="unique_coaching_request")
        ]

    def __str__(self):
        return f"{self.coachee} -> {self.coach} ({self.status})"


class CoachingRelationship(TimeStampedModel):
    """A confirmed coach/coachee pairing — created by
    apps.coaching.services.accept_coaching_request, never directly.

    `ended_at` soft-ends the relationship (never hard-deleted) so both
    sides keep the historical fact they were once coach/client —
    apps.coaching.services.end_coaching_relationship is the only place
    that sets it. A coach and coachee may have more than one row over
    time (ended, then started again later); the partial unique
    constraint below only ever blocks a *second simultaneously active*
    row for the same pair, mirroring NutritionTarget/DietPlan's own
    "only one open row" partial constraints elsewhere in this
    codebase. A coachee may have several *different* active coaches at
    once (e.g. a nutrition coach and a separate strength coach) —
    nothing here restricts that, only the same coach twice.
    """

    coach = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="coaching_clients", on_delete=models.CASCADE
    )
    coachee = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="coaches", on_delete=models.CASCADE
    )
    ended_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["coach", "coachee"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_active_coaching_relationship",
            )
        ]

    def __str__(self):
        state = "active" if self.ended_at is None else "ended"
        return f"{self.coach} coaches {self.coachee} ({state})"

    @property
    def is_active(self):
        return self.ended_at is None
