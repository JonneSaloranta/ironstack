from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView

from apps.programs import services as program_services
from apps.programs.models import Workout

# apps.progression depends on apps.workouts (it reads session/set history),
# so apps.workouts itself must never import from apps.progression at the
# services/models layer — that would be a cycle. The view layer is the one
# place allowed to cross that boundary, same reasoning as apps.records
# above: views orchestrate across apps, per docs/ARCHITECTURE.md.
from apps.progression.suggestions import suggest_weight
from apps.records import services as records_services

from . import services
from .forms import ExerciseSetForm, PerformedExerciseAddForm
from .models import ExerciseSet, PerformedExercise, WorkoutSessionStatus


class WorkoutSessionListView(LoginRequiredMixin, ListView):
    template_name = "workouts/session_list.html"
    context_object_name = "sessions"

    def get_queryset(self):
        return services.sessions_for(self.request.user).select_related("workout", "program")


class WorkoutSessionDetailView(LoginRequiredMixin, DetailView):
    template_name = "workouts/session_detail.html"
    context_object_name = "session"

    def get_queryset(self):
        return services.sessions_for(self.request.user).prefetch_related(
            "performed_exercises__exercise", "performed_exercises__sets"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # Attached per-row rather than passed as a separate dict so the
        # template can do `pe.set_form` — Django template dict lookups
        # can't take a variable key (`dict.pe.id` won't resolve `pe.id`).
        for performed_exercise in self.object.performed_exercises.all():
            performed_exercise.set_form, performed_exercise.suggestion = _build_set_form(
                self.request.user, performed_exercise, session=self.object
            )
        return context


def session_start(request, workout_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    workout = get_object_or_404(
        Workout, pk=workout_pk, program__in=program_services.visible_to(request.user)
    )
    session = services.start_session(request.user, workout=workout)
    return redirect("workouts:session-detail", pk=session.pk)


def session_start_freeform(request):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    session = services.start_session(request.user, workout=None)
    return redirect("workouts:session-detail", pk=session.pk)


def session_complete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    session = get_object_or_404(services.sessions_for(request.user), pk=pk)
    services.complete_session(session)
    return redirect("workouts:session-detail", pk=session.pk)


def session_abandon(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    session = get_object_or_404(services.sessions_for(request.user), pk=pk)
    services.abandon_session(session)
    return redirect("workouts:session-detail", pk=session.pk)


def performed_exercise_add(request, session_pk):
    session = get_object_or_404(
        services.sessions_for(request.user),
        pk=session_pk,
        status=WorkoutSessionStatus.IN_PROGRESS,
    )
    form = PerformedExerciseAddForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        services.add_performed_exercise(session, form.cleaned_data["exercise"])
        return redirect("workouts:session-detail", pk=session.pk)
    return render(
        request, "workouts/performed_exercise_form.html", {"form": form, "session": session}
    )


def _owned_performed_exercise_or_404(request, pk):
    return get_object_or_404(
        PerformedExercise.objects.select_related("session"),
        pk=pk,
        session__user=request.user,
    )


def _build_set_form(user, performed_exercise, session=None):
    """A set-log form pre-filled with the best default available: a smart
    suggestion for the exercise's first set (docs/SMART_SUGGESTIONS.md),
    falling back to repeating the last logged set for subsequent ones —
    see apps.workouts.services.default_set_values. The suggestion is only
    ever a form default; nothing here stops the user entering something
    else before submitting ("never prevent a user from entering a
    different value").

    `session` lets a caller that already has it (the detail view, looping
    over one session's performed exercises) avoid an extra query;
    function-based views fall back to `performed_exercise.session`.

    Returns `(form, suggestion)` — `suggestion` is None once a set has
    already been logged this exercise (nothing left to suggest toward),
    the session is no longer in progress (nothing to log at all), or the
    exercise has no prescription behind it (freeform/ad hoc additions
    have no configured progression method).
    """
    session = session or performed_exercise.session
    initial = services.default_set_values(performed_exercise)
    suggestion = None
    already_logged = bool(performed_exercise.sets.all())
    if not already_logged and session.is_in_progress and performed_exercise.prescription_id:
        suggestion = suggest_weight(user, performed_exercise.prescription)
        if suggestion.suggested_weight is not None:
            initial["weight"] = suggestion.suggested_weight
        if suggestion.target_min_reps is not None:
            initial["reps"] = suggestion.target_min_reps
    return ExerciseSetForm(initial=initial), suggestion


def set_log(request, performed_exercise_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    performed_exercise = _owned_performed_exercise_or_404(request, performed_exercise_pk)
    form = ExerciseSetForm(request.POST)
    new_prs = []
    suggestion = None
    if not performed_exercise.session.is_in_progress:
        form.add_error(None, "This session is no longer in progress.")
    elif form.is_valid():
        logged_set = services.log_set(performed_exercise, **form.cleaned_data)
        new_prs = records_services.check_and_record_prs(logged_set)
        for record in new_prs:
            messages.success(
                request,
                f"New PR — {performed_exercise.exercise.name}: "
                f"{record.get_record_type_display()} {record.value}",
            )
        form, suggestion = _build_set_form(request.user, performed_exercise)
    return _render_session_or_card(
        request, performed_exercise, form, new_prs=new_prs, suggestion=suggestion
    )


def set_edit(request, pk):
    exercise_set = get_object_or_404(
        ExerciseSet, pk=pk, performed_exercise__session__user=request.user
    )
    performed_exercise = exercise_set.performed_exercise
    if request.method == "POST":
        form = ExerciseSetForm(request.POST, instance=exercise_set)
        if form.is_valid():
            form.save()
            new_form, suggestion = _build_set_form(request.user, performed_exercise)
            return _render_session_or_card(
                request, performed_exercise, new_form, suggestion=suggestion
            )
    else:
        form = ExerciseSetForm(instance=exercise_set)
    return render(
        request,
        "workouts/set_edit_form.html",
        {"form": form, "exercise_set": exercise_set, "session": performed_exercise.session},
    )


def set_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    exercise_set = get_object_or_404(
        ExerciseSet, pk=pk, performed_exercise__session__user=request.user
    )
    performed_exercise = exercise_set.performed_exercise
    exercise_set.delete()
    form, suggestion = _build_set_form(request.user, performed_exercise)
    return _render_session_or_card(request, performed_exercise, form, suggestion=suggestion)


def _render_session_or_card(request, performed_exercise, set_form, new_prs=None, suggestion=None):
    if request.headers.get("HX-Request"):
        return render(
            request,
            "workouts/_performed_exercise_card.html",
            {
                "pe": performed_exercise,
                "set_form": set_form,
                "session": performed_exercise.session,
                "new_prs": new_prs or [],
                "suggestion": suggestion,
            },
        )
    return redirect("workouts:session-detail", pk=performed_exercise.session_id)
