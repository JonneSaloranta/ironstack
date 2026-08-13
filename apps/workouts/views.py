from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.views.generic import DetailView, ListView

from apps.programs import services as program_services
from apps.programs.models import Workout
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
            performed_exercise.set_form = ExerciseSetForm(
                initial=services.default_set_values(performed_exercise)
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


def set_log(request, performed_exercise_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    performed_exercise = _owned_performed_exercise_or_404(request, performed_exercise_pk)
    form = ExerciseSetForm(request.POST)
    new_prs = []
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
        form = ExerciseSetForm(initial=services.default_set_values(performed_exercise))
    return _render_session_or_card(request, performed_exercise, form, new_prs=new_prs)


def set_edit(request, pk):
    exercise_set = get_object_or_404(
        ExerciseSet, pk=pk, performed_exercise__session__user=request.user
    )
    performed_exercise = exercise_set.performed_exercise
    if request.method == "POST":
        form = ExerciseSetForm(request.POST, instance=exercise_set)
        if form.is_valid():
            form.save()
            new_form = ExerciseSetForm(initial=services.default_set_values(performed_exercise))
            return _render_session_or_card(request, performed_exercise, new_form)
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
    form = ExerciseSetForm(initial=services.default_set_values(performed_exercise))
    return _render_session_or_card(request, performed_exercise, form)


def _render_session_or_card(request, performed_exercise, set_form, new_prs=None):
    if request.headers.get("HX-Request"):
        return render(
            request,
            "workouts/_performed_exercise_card.html",
            {
                "pe": performed_exercise,
                "set_form": set_form,
                "session": performed_exercise.session,
                "new_prs": new_prs or [],
            },
        )
    return redirect("workouts:session-detail", pk=performed_exercise.session_id)
