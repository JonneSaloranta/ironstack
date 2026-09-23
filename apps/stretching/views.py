import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.translation import gettext as _
from django.views.generic import ListView, TemplateView

from apps.core.pagination import paginate_list
from apps.exercises.models import MuscleGroup
from apps.workouts.models import WorkoutSession, WorkoutSessionStatus

from . import services
from .forms import QuickLogForm, RoutineItemForm, StretchForm, StretchRoutineForm
from .models import (
    PerformedStretch,
    PerformedStretchStatus,
    RoutineItem,
    Stretch,
    StretchKind,
    StretchRoutine,
    StretchSession,
    StretchSessionStatus,
)


def _post_only(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    return None


def _visible_routine_or_404(request, pk):
    return get_object_or_404(
        services.visible_routines(request.user, include_inactive=True), pk=pk
    )


def _owned_routine_or_404(request, pk):
    return get_object_or_404(StretchRoutine, pk=pk, owner=request.user)


def _owned_item_or_404(request, routine_pk, pk):
    routine = _owned_routine_or_404(request, routine_pk)
    return routine, get_object_or_404(RoutineItem, pk=pk, routine=routine)


# --- Overview -------------------------------------------------------------


class StretchingHomeView(LoginRequiredMixin, TemplateView):
    template_name = "stretching/home.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        routines = list(services.visible_routines(user).prefetch_related("items__stretch"))
        for routine in routines:
            routine.planned_seconds = services.routine_seconds(routine)
        context["routines"] = routines
        context["active_session"] = services.active_session(user)
        context["summary"] = services.summarize(user)
        context["weekly_chart"] = services.weekly_chart(user)
        context["recent_sessions"] = services.completed_sessions(user)[:5]
        return context


# --- Routines -------------------------------------------------------------


class RoutineListView(LoginRequiredMixin, ListView):
    template_name = "stretching/routine_list.html"
    context_object_name = "routines"

    def get_queryset(self):
        routines = list(
            services.visible_routines(self.request.user).prefetch_related("items__stretch")
        )
        for routine in routines:
            routine.planned_seconds = services.routine_seconds(routine)
        return routines


@login_required
def routine_create(request):
    form = StretchRoutineForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        routine = form.save()
        messages.success(request, _("Routine created. Now add some stretches."))
        return redirect("stretching:routine-detail", pk=routine.pk)
    return render(request, "stretching/routine_form.html", {"form": form})


@login_required
def routine_detail(request, pk):
    routine = _visible_routine_or_404(request, pk)
    items = list(routine.items.select_related("stretch"))
    for item in items:
        item.planned_seconds = services.item_seconds(
            hold_seconds=item.hold_seconds,
            sets=item.sets,
            per_side=item.stretch.per_side,
            rest_seconds=item.rest_seconds,
        )
    can_edit = routine.owner_id == request.user.id
    return render(
        request,
        "stretching/routine_detail.html",
        {
            "routine": routine,
            "items": items,
            "planned_seconds": sum(item.planned_seconds for item in items),
            "can_edit": can_edit,
            "add_form": RoutineItemForm(user=request.user) if can_edit else None,
        },
    )


@login_required
def routine_update(request, pk):
    routine = _owned_routine_or_404(request, pk)
    form = StretchRoutineForm(request.POST or None, instance=routine, owner=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Routine saved."))
        return redirect("stretching:routine-detail", pk=routine.pk)
    return render(request, "stretching/routine_form.html", {"form": form, "routine": routine})


@login_required
def routine_deactivate(request, pk):
    if (response := _post_only(request)) is not None:
        return response
    routine = _owned_routine_or_404(request, pk)
    routine.active = False
    routine.save(update_fields=["active", "updated_at"])
    messages.success(request, _("Routine removed."))
    return redirect("stretching:routine-list")


@login_required
def routine_copy(request, pk):
    if (response := _post_only(request)) is not None:
        return response
    copy = services.copy_routine(_visible_routine_or_404(request, pk), request.user)
    messages.success(request, _("Copied — this one is yours to edit."))
    return redirect("stretching:routine-detail", pk=copy.pk)


@login_required
def routine_item_add(request, pk):
    routine = _owned_routine_or_404(request, pk)
    form = RoutineItemForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        services.add_routine_item(
            routine,
            data["stretch"],
            hold_seconds=data["hold_seconds"],
            sets=data["sets"],
            rest_seconds=data["rest_seconds"],
        )
        messages.success(request, _("Stretch added."))
        return redirect("stretching:routine-detail", pk=routine.pk)
    return render(
        request, "stretching/routine_item_form.html", {"form": form, "routine": routine}
    )


@login_required
def routine_item_update(request, routine_pk, pk):
    routine, item = _owned_item_or_404(request, routine_pk, pk)
    form = RoutineItemForm(request.POST or None, instance=item, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Stretch saved."))
        return redirect("stretching:routine-detail", pk=routine.pk)
    return render(
        request,
        "stretching/routine_item_form.html",
        {"form": form, "routine": routine, "item": item},
    )


@login_required
def routine_item_delete(request, routine_pk, pk):
    if (response := _post_only(request)) is not None:
        return response
    routine, item = _owned_item_or_404(request, routine_pk, pk)
    item.delete()
    messages.success(request, _("Stretch removed."))
    return redirect("stretching:routine-detail", pk=routine.pk)


@login_required
def routine_item_move(request, routine_pk, pk):
    if (response := _post_only(request)) is not None:
        return response
    routine, item = _owned_item_or_404(request, routine_pk, pk)
    direction = -1 if request.POST.get("direction") == "up" else 1
    services.move_routine_item(item, direction)
    return redirect(reverse("stretching:routine-detail", args=[routine.pk]) + f"#item-{item.pk}")


# --- Stretch library ------------------------------------------------------


@login_required
def stretch_list(request):
    stretches = services.visible_stretches(request.user).prefetch_related("muscle_groups")
    muscle_group = request.GET.get("muscle_group", "")
    kind = request.GET.get("kind", "")
    if muscle_group.isdigit():
        stretches = stretches.filter(muscle_groups=muscle_group)
    if kind in StretchKind.values:
        stretches = stretches.filter(kind=kind)
    return render(
        request,
        "stretching/stretch_list.html",
        {
            "stretches": stretches,
            "muscle_groups": MuscleGroup.objects.filter(stretches__isnull=False).distinct(),
            "kinds": StretchKind.choices,
            "selected_muscle_group": muscle_group,
            "selected_kind": kind,
        },
    )


@login_required
def stretch_detail(request, pk):
    stretch = get_object_or_404(
        services.visible_stretches(request.user, include_inactive=True), pk=pk
    )
    return render(
        request,
        "stretching/stretch_detail.html",
        {"stretch": stretch, "can_edit": stretch.owner_id == request.user.id},
    )


@login_required
def stretch_create(request):
    form = StretchForm(request.POST or None, owner=request.user)
    if request.method == "POST" and form.is_valid():
        stretch = form.save()
        messages.success(request, _("Stretch created."))
        return redirect("stretching:stretch-detail", pk=stretch.pk)
    return render(request, "stretching/stretch_form.html", {"form": form})


@login_required
def stretch_update(request, pk):
    stretch = get_object_or_404(Stretch, pk=pk, owner=request.user)
    form = StretchForm(request.POST or None, instance=stretch, owner=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Stretch saved."))
        return redirect("stretching:stretch-detail", pk=stretch.pk)
    return render(request, "stretching/stretch_form.html", {"form": form, "stretch": stretch})


@login_required
def stretch_deactivate(request, pk):
    if (response := _post_only(request)) is not None:
        return response
    stretch = get_object_or_404(Stretch, pk=pk, owner=request.user)
    stretch.active = False
    stretch.save(update_fields=["active"])
    messages.success(request, _("Stretch removed from your library."))
    return redirect("stretching:stretch-list")


# --- Sessions -------------------------------------------------------------


def _owned_session_or_404(request, pk, **filters):
    return get_object_or_404(StretchSession, pk=pk, user=request.user, **filters)


def _start(request, **kwargs):
    try:
        session = services.start_session(request.user, **kwargs)
    except services.SessionAlreadyInProgress as exc:
        messages.info(request, _("You already have a stretching session in progress."))
        session = exc.session
    return redirect("stretching:session-play", pk=session.pk)


@login_required
def session_start(request, routine_pk):
    if (response := _post_only(request)) is not None:
        return response
    routine = get_object_or_404(services.visible_routines(request.user), pk=routine_pk)
    if not routine.items.exists():
        messages.error(request, _("This routine has no stretches yet."))
        return redirect("stretching:routine-detail", pk=routine.pk)
    return _start(request, routine=routine)


@login_required
def session_start_cooldown(request, workout_session_pk):
    """Start the cool-down services.suggest_cooldown proposes for one of
    the user's own finished workouts. Re-derived here rather than trusted
    from the form, so the POST can't smuggle in someone else's routine."""
    if (response := _post_only(request)) is not None:
        return response
    workout_session = get_object_or_404(
        WorkoutSession,
        pk=workout_session_pk,
        user=request.user,
        status=WorkoutSessionStatus.COMPLETED,
    )
    suggestion = services.suggest_cooldown(workout_session)
    if suggestion is None:
        messages.error(request, _("No cool-down stretches match this workout."))
        return redirect("workouts:session-detail", pk=workout_session.pk)
    if suggestion.routine is not None:
        return _start(request, routine=suggestion.routine, after_workout=workout_session)
    return _start(
        request,
        stretches=suggestion.stretches,
        name=_("Cool-down"),
        after_workout=workout_session,
    )


@login_required
def session_play(request, pk):
    session = _owned_session_or_404(request, pk)
    if session.status != StretchSessionStatus.IN_PROGRESS:
        return redirect("stretching:session-detail", pk=session.pk)
    performed = list(session.performed_stretches.select_related("stretch"))
    return render(
        request,
        "stretching/session_play.html",
        {
            "session": session,
            "performed": performed,
            "pending_count": sum(
                1 for item in performed if item.status == PerformedStretchStatus.PENDING
            ),
            "timer_steps": services.timer_steps(session),
        },
    )


@login_required
def performed_mark(request, pk, performed_pk):
    """Mark one stretch done or skipped — from the guided timer (a JSON
    fetch, answered with 204) or the plain per-stretch buttons (a normal
    form POST, answered with a redirect back to the session)."""
    if (response := _post_only(request)) is not None:
        return response
    session = _owned_session_or_404(request, pk, status=StretchSessionStatus.IN_PROGRESS)
    performed = get_object_or_404(PerformedStretch, pk=performed_pk, session=session)
    if request.content_type == "application/json":
        try:
            payload = json.loads(request.body or b"{}")
        except ValueError:
            payload = {}
    else:
        payload = request.POST
    done = payload.get("action", "done") != "skip"
    try:
        actual_seconds = max(0, int(payload.get("actual_seconds")))
    except (TypeError, ValueError):
        actual_seconds = None
    services.mark_performed(performed, done=done, actual_seconds=actual_seconds)
    if request.content_type == "application/json":
        return HttpResponse(status=204)
    return redirect("stretching:session-play", pk=session.pk)


@login_required
def session_complete(request, pk):
    if (response := _post_only(request)) is not None:
        return response
    session = _owned_session_or_404(request, pk, status=StretchSessionStatus.IN_PROGRESS)
    services.complete_session(session)
    messages.success(request, _("Nicely done — stretching session saved."))
    return redirect("stretching:session-detail", pk=session.pk)


@login_required
def session_abandon(request, pk):
    if (response := _post_only(request)) is not None:
        return response
    session = _owned_session_or_404(request, pk, status=StretchSessionStatus.IN_PROGRESS)
    services.abandon_session(session)
    messages.info(request, _("Session abandoned."))
    return redirect("stretching:home")


# --- History & quick log --------------------------------------------------


@login_required
def session_history(request):
    sessions = services.sessions_for(request.user).exclude(
        status=StretchSessionStatus.IN_PROGRESS
    )
    return render(
        request,
        "stretching/session_history.html",
        {"page_obj": paginate_list(request, list(sessions))},
    )


@login_required
def session_detail(request, pk):
    session = _owned_session_or_404(request, pk)
    if session.status == StretchSessionStatus.IN_PROGRESS:
        return redirect("stretching:session-play", pk=session.pk)
    return render(
        request,
        "stretching/session_detail.html",
        {"session": session, "performed": session.performed_stretches.all()},
    )


@login_required
def quick_log(request):
    form = QuickLogForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Stretching logged."))
        return redirect("stretching:session-history")
    return render(request, "stretching/session_form.html", {"form": form})


@login_required
def session_edit(request, pk):
    session = _owned_session_or_404(request, pk, status=StretchSessionStatus.COMPLETED)
    form = QuickLogForm(request.POST or None, instance=session, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, _("Session updated."))
        return redirect("stretching:session-detail", pk=session.pk)
    return render(request, "stretching/session_form.html", {"form": form, "session": session})


@login_required
def session_delete(request, pk):
    if (response := _post_only(request)) is not None:
        return response
    session = _owned_session_or_404(request, pk)
    if session.status == StretchSessionStatus.IN_PROGRESS:
        return redirect("stretching:session-play", pk=session.pk)
    session.delete()
    messages.success(request, _("Session deleted."))
    return redirect("stretching:session-history")
