"""All-time "look what everyone's done" highlights for the dashboard
achievements carousel (docs/UI.md "Achievements carousel") — deliberately
separate from apps.analytics.services, which is entirely date-range-
scoped (apps.analytics.dateranges) and per-user: every figure here is a
lifetime total, and the carousel itself is shared across every user on
this instance, not personal to whoever's viewing it — a self-hosted app
used by, e.g., one household or a small gym, where seeing a housemate's
new streak is the point (see `User.show_achievements` for the opt-out).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from django.utils.translation import gettext, ngettext

from apps.core import units as core_units
from apps.records.models import PersonalRecord
from apps.workouts.models import ExerciseSet, WorkoutSession, WorkoutSessionStatus

# How recent counts as "recent" for RecentActivity.is_recent's green-dot
# tier below — a fitness-specific judgment call (not a config value or a
# per-user preference, since there's no principled way for a user to
# tune "how impressive is this"), distinct from is_in_progress's own
# "training right now" tier.
_RECENT_ACTIVITY_WINDOW = timedelta(hours=24)


@dataclass(frozen=True)
class Achievement:
    icon: str
    label: str
    value: str
    username: str


@dataclass(frozen=True)
class RecentActivity:
    username: str
    last_active_at: datetime  # the latest session's started_at
    is_in_progress: bool
    is_recent: bool


def longest_workout_streak_days(user):
    """The longest run of *consecutive calendar days* with at least one
    completed workout, ever — not the user's current streak, which
    changes daily and wouldn't read as a stable "achievement" the way a
    personal-best figure does. Computed in Python over the (small,
    per-user) list of distinct workout dates rather than a SQL window
    function — docs/ANALYTICS.md: start with straightforward ORM
    queries, only add more once profiling demonstrates a need.
    """
    dates = sorted(
        set(
            WorkoutSession.objects.filter(
                user=user, status=WorkoutSessionStatus.COMPLETED
            ).values_list("started_at__date", flat=True)
        )
    )
    if not dates:
        return 0
    longest = current = 1
    for previous_date, date in zip(dates, dates[1:]):
        if (date - previous_date).days == 1:
            current += 1
            longest = max(longest, current)
        else:
            current = 1
    return longest


def _highlights_for(user):
    """The up-to-4 highlight cards for a single user — `[]` if they have
    no completed workouts yet (nothing to celebrate)."""
    total_workouts = WorkoutSession.objects.filter(
        user=user, status=WorkoutSessionStatus.COMPLETED
    ).count()
    if total_workouts == 0:
        return []

    streak_days = longest_workout_streak_days(user)
    total_prs = PersonalRecord.objects.filter(user=user).count()
    total_volume_kg = sum(
        (
            exercise_set.weight * exercise_set.reps
            for exercise_set in ExerciseSet.objects.filter(
                performed_exercise__session__user=user,
                performed_exercise__session__status=WorkoutSessionStatus.COMPLETED,
                is_warmup=False,
            )
        ),
        Decimal("0"),
    )
    unit_system = getattr(user, "unit_system", "metric")

    highlights = [
        Achievement(
            icon="streak",
            label=gettext("Longest streak"),
            value=ngettext("%(counter)s day", "%(counter)s days", streak_days)
            % {"counter": streak_days},
            username=user.username,
        ),
        Achievement(
            icon="workouts",
            # Reuses the same msgid/msgid_plural pair
            # templates/programs/program_list.html's per-program
            # workout count already uses (see locale .po files) — one
            # fewer pair to translate, same wording.
            label=gettext("Workouts completed"),
            value=ngettext("%(counter)s workout", "%(counter)s workouts", total_workouts)
            % {"counter": total_workouts},
            username=user.username,
        ),
    ]
    if total_prs:
        highlights.append(
            Achievement(
                icon="pr",
                label=gettext("Personal records"),
                value=ngettext("%(counter)s PR", "%(counter)s PRs", total_prs)
                % {"counter": total_prs},
                username=user.username,
            )
        )
    if total_volume_kg:
        highlights.append(
            Achievement(
                icon="volume",
                label=gettext("Total weight lifted"),
                value=(
                    f"{core_units.kg_to_display(total_volume_kg, unit_system)} "
                    f"{core_units.weight_unit_label(unit_system)}"
                ),
                username=user.username,
            )
        )
    return highlights


def achievement_highlights():
    """Every opted-in user's highlights, concatenated — see this
    module's docstring for why this isn't scoped to a single viewer.
    `User.show_achievements=False` excludes that user's own achievements
    from the result entirely (their own view of the carousel included —
    it's a privacy setting, "don't show my stats to anyone", not a
    personal "hide the carousel from me" toggle). Ordered by username
    for a stable, predictable rotation rather than by anything that
    would change between requests.
    """
    User = get_user_model()
    highlights = []
    for user in User.objects.filter(show_achievements=True).order_by("username"):
        highlights.extend(_highlights_for(user))
    return highlights


def recently_active_users(limit=10):
    """The dashboard's "Recently active" list (docs/UI.md) — every
    opted-in user (`User.show_achievements` — the same "share my
    training activity" setting the achievements carousel uses; see its
    docstring) who has started at least one workout session, most
    recently active first. Counts *any* session status, not just
    completed ones: starting a workout is itself a sign of activity,
    and it's what lets an in-progress session surface as "training now"
    rather than just an ordinary timestamp.

    `limit` keeps this to a glanceable size rather than a wall of
    usernames on an instance with many users — this app is typically
    self-hosted for one household or a small gym, so this is a modest
    default cap, not a paginated feature.
    """
    User = get_user_model()
    now = timezone.now()
    activity = []
    for user in User.objects.filter(show_achievements=True):
        latest = WorkoutSession.objects.filter(user=user).order_by("-started_at").first()
        if latest is None:
            continue
        activity.append(
            RecentActivity(
                username=user.username,
                last_active_at=latest.started_at,
                is_in_progress=latest.status == WorkoutSessionStatus.IN_PROGRESS,
                is_recent=(now - latest.started_at) <= _RECENT_ACTIVITY_WINDOW,
            )
        )
    activity.sort(key=lambda entry: entry.last_active_at, reverse=True)
    return activity[:limit] if limit else activity
