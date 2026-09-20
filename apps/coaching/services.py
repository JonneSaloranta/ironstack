"""Every rule about who may request/accept/end a coaching relationship,
and what a coach's shared programs/diet plans actually let a client
do, lives here — not in views or templates (CLAUDE.md: "keep business/
domain logic out of Django views"). Each mutating function either
succeeds or raises `CoachingError` with a translated, user-facing
message a view can show directly — same shape as
`apps.social.services.SocialError`.
"""

import difflib
import json

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from .models import CoachingRelationship, CoachingRequest, CoachingRequestStatus


class CoachingError(Exception):
    """Raised for any rule violation above a plain 404/permission
    check — a view catches this and re-renders/redirects with
    `str(error)` as a flash message, rather than every caller
    re-deriving the same checks."""


def active_coach_ids_for(user):
    """Every user id currently, actively coaching `user` — the one
    query apps.programs.services.visible_to/apps.nutrition.services.
    diet_plans_visible_to both filter against, so a coach's own shared
    library only ever appears for someone actually in an active
    relationship with them."""
    return set(
        CoachingRelationship.objects.filter(coachee=user, ended_at__isnull=True).values_list(
            "coach_id", flat=True
        )
    )


def coaches_of(coachee):
    """Every active CoachingRelationship where `coachee` is being
    coached, most recently started first — select_related so
    templates/coaching/my_coaches.html can read `.coach` for free."""
    return CoachingRelationship.objects.filter(
        coachee=coachee, ended_at__isnull=True
    ).select_related("coach")


def clients_of(coach):
    """Every active CoachingRelationship where `coach` is coaching
    someone — the PT's own "My clients" list."""
    return CoachingRelationship.objects.filter(
        coach=coach, ended_at__isnull=True
    ).select_related("coachee")


def get_active_relationship(coach, coachee):
    return CoachingRelationship.objects.filter(
        coach=coach, coachee=coachee, ended_at__isnull=True
    ).first()


def is_active_relationship(coach, coachee) -> bool:
    return CoachingRelationship.objects.filter(
        coach=coach, coachee=coachee, ended_at__isnull=True
    ).exists()


@transaction.atomic
def send_coaching_request(coachee, coach):
    if coachee == coach:
        raise CoachingError(_("You can't request yourself as a coach."))
    if not coach.is_personal_trainer:
        raise CoachingError(_("This user isn't a personal trainer."))
    if not coach.accepting_new_clients:
        raise CoachingError(_("This coach isn't accepting new clients right now."))
    if is_active_relationship(coach, coachee):
        raise CoachingError(_("This person is already coaching you."))
    existing = CoachingRequest.objects.filter(coach=coach, coachee=coachee).first()
    if existing and existing.status == CoachingRequestStatus.PENDING:
        raise CoachingError(_("You already sent a coaching request to this trainer."))
    if existing:
        existing.status = CoachingRequestStatus.PENDING
        existing.responded_at = None
        existing.save(update_fields=["status", "responded_at"])
        return existing
    return CoachingRequest.objects.create(coach=coach, coachee=coachee)


@transaction.atomic
def accept_coaching_request(coaching_request, acting_user):
    if coaching_request.coach_id != acting_user.pk:
        raise CoachingError(_("You can't respond to this coaching request."))
    if coaching_request.status != CoachingRequestStatus.PENDING:
        raise CoachingError(_("This coaching request has already been answered."))
    coaching_request.status = CoachingRequestStatus.ACCEPTED
    coaching_request.responded_at = timezone.now()
    coaching_request.save(update_fields=["status", "responded_at"])
    # Not get_or_create(ended_at__isnull=True, ...) — that lookup isn't
    # a valid field to also pass to CoachingRelationship's constructor.
    # A pending CoachingRequest already being PENDING (checked above)
    # is what stops this from ever running twice for the same pair in
    # practice, the same single-check reliance apps.social.services.
    # accept_friend_request's own Friendship.get_or_create rests on.
    if not is_active_relationship(coaching_request.coach, coaching_request.coachee):
        CoachingRelationship.objects.create(
            coach=coaching_request.coach, coachee=coaching_request.coachee
        )
    return coaching_request


def decline_coaching_request(coaching_request, acting_user):
    if coaching_request.coach_id != acting_user.pk:
        raise CoachingError(_("You can't respond to this coaching request."))
    if coaching_request.status != CoachingRequestStatus.PENDING:
        raise CoachingError(_("This coaching request has already been answered."))
    coaching_request.status = CoachingRequestStatus.DECLINED
    coaching_request.responded_at = timezone.now()
    coaching_request.save(update_fields=["status", "responded_at"])
    return coaching_request


def end_coaching_relationship(relationship, acting_user):
    if acting_user.pk not in (relationship.coach_id, relationship.coachee_id):
        raise CoachingError(_("You're not part of this coaching relationship."))
    if relationship.ended_at is not None:
        raise CoachingError(_("This coaching relationship has already ended."))
    relationship.ended_at = timezone.now()
    relationship.save(update_fields=["ended_at"])
    return relationship


def pending_coaching_request_count(coach):
    return CoachingRequest.objects.filter(coach=coach, status=CoachingRequestStatus.PENDING).count()


def has_pending_coaching_requests(coach):
    return CoachingRequest.objects.filter(
        coach=coach, status=CoachingRequestStatus.PENDING
    ).exists()


@transaction.atomic
def import_program_from_coach(source, coachee):
    """The client-facing half of a PT's shared program library: an
    independent copy for `coachee`, exactly like `apps.programs.
    services.copy_program` already gives anyone copying a system
    template — plus the lineage (`imported_from`/`coach_snapshot`/
    `coach_snapshot_version`) `apply_program_update` below needs to
    later offer "your coach updated this"."""
    from apps.programs.services import copy_program, export_program

    if not source.shared_with_clients.filter(pk=coachee.pk).exists():
        raise CoachingError(_("This program hasn't been shared with you."))
    if not is_active_relationship(source.owner, coachee):
        raise CoachingError(_("You're not an active client of this program's coach."))
    copy = copy_program(source, owner=coachee)
    copy.imported_from = source
    copy.coach_snapshot = export_program(source)
    copy.coach_snapshot_version = source.version
    copy.save(update_fields=["imported_from", "coach_snapshot", "coach_snapshot_version"])
    return copy


@transaction.atomic
def import_diet_plan_from_coach(source, coachee):
    """Same shape as `import_program_from_coach`, wrapping
    `apps.nutrition.services.import_diet_plan` (already independent,
    already lands `is_active=False`) rather than a new deep-copy."""
    from apps.nutrition.services import export_diet_plan, import_diet_plan

    if not source.shared_with_clients.filter(pk=coachee.pk).exists():
        raise CoachingError(_("This diet plan hasn't been shared with you."))
    if not is_active_relationship(source.user, coachee):
        raise CoachingError(_("You're not an active client of this diet plan's coach."))
    payload = export_diet_plan(source)
    copy = import_diet_plan(coachee, payload)
    copy.imported_from = source
    copy.coach_snapshot = payload
    copy.coach_snapshot_version = source.version
    copy.save(update_fields=["imported_from", "coach_snapshot", "coach_snapshot_version"])
    return copy


def _snapshot_diff(before, after):
    """`difflib.unified_diff` over key-sorted, indented JSON — reuses
    `export_program`/`export_diet_plan`'s already-tested serialization
    instead of a bespoke structural differ, the same "diff the JSON,
    don't diff the object graph" approach apps.core.data_exchange's
    whole export format already implies."""
    before_lines = json.dumps(before, indent=2, sort_keys=True).splitlines(keepends=True)
    after_lines = json.dumps(after, indent=2, sort_keys=True).splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile=str(_("your current copy")),
            tofile=str(_("coach's update")),
        )
    )


def program_update_available(program) -> bool:
    """Cheap int compare, not a diff — safe to call once per row on a
    plan list page. False whenever `imported_from` is gone (SET_NULL
    after the source program, or its owner's whole account, was
    deleted) — there's nothing left to update from."""
    return (
        program.imported_from_id is not None
        and program.imported_from.version != program.coach_snapshot_version
    )


def program_update_diff(program) -> str:
    from apps.programs.services import export_program

    return _snapshot_diff(program.coach_snapshot, export_program(program.imported_from))


@transaction.atomic
def apply_program_update(program, acting_user):
    """Overwrites `program`'s Workouts/ExercisePrescriptions with the
    coach's current shape, via `_replace_program_contents` — the exact
    mechanism `apps.programs.views.workout_delete`/`prescription_delete`
    already prove safe against `WorkoutSession` history (both hold
    only `SET_NULL` informational backlinks, snapshotting everything
    else onto `PerformedExercise` at session-start).

    Only structure is replaced — `program.name`/`description` (which
    the client may have personalized) are left untouched. This
    discards any edits the client made to their own copy's workouts
    since import; that's inherent to "apply the coach's current
    version", and exactly what `program_update_diff` is for showing
    before this runs. There is no separate "skip" action to perform —
    not calling this is skipping."""
    from apps.programs.services import _replace_program_contents, export_program

    if program.owner_id != acting_user.pk:
        raise CoachingError(_("You can't update this program."))
    source = program.imported_from
    if source is None:
        raise CoachingError(_("This program's coach source is no longer available."))
    _replace_program_contents(program, source)
    program.coach_snapshot = export_program(source)
    program.coach_snapshot_version = source.version
    program.bump_version()
    program.save(update_fields=["coach_snapshot", "coach_snapshot_version"])
    return program


def diet_plan_update_available(plan) -> bool:
    return (
        plan.imported_from_id is not None
        and plan.imported_from.version != plan.coach_snapshot_version
    )


def diet_plan_update_diff(plan) -> str:
    from apps.nutrition.services import export_diet_plan

    return _snapshot_diff(plan.coach_snapshot, export_diet_plan(plan.imported_from))


@transaction.atomic
def apply_diet_plan_update(plan, acting_user):
    """Diet-plan equivalent of `apply_program_update` — clears `plan`'s
    own meals (cascades to their items) and rebuilds from the coach's
    current shape via `_populate_diet_plan`, the same helper
    `apps.nutrition.services.import_diet_plan` itself uses."""
    from apps.nutrition.services import _populate_diet_plan, export_diet_plan

    if plan.user_id != acting_user.pk:
        raise CoachingError(_("You can't update this diet plan."))
    source = plan.imported_from
    if source is None:
        raise CoachingError(_("This diet plan's coach source is no longer available."))
    plan.meals.all().delete()
    payload = export_diet_plan(source)
    _populate_diet_plan(plan, payload)
    plan.coach_snapshot = payload
    plan.coach_snapshot_version = source.version
    plan.bump_version()
    plan.save(update_fields=["coach_snapshot", "coach_snapshot_version"])
    return plan
