import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _
from django.views.generic import DetailView, ListView

from apps.core import units as core_units
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

# Training mode's rest-timer default (docs/UI.md "Training mode") — a
# fixed UI convenience, not a domain/progression rule, so it lives here
# rather than as a per-prescription model field; the timer widget itself
# lets a user adjust or skip it freely for any single rest either way.
REST_SECONDS = 90


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


@login_required
def session_train(request, pk):
    """Explicit `@login_required`, unlike this module's other function
    views: `services.sessions_for(request.user)` crashes rather than
    returning an empty queryset for an anonymous `AnonymousUser`
    (Django's ORM rejects filtering a ForeignKey against it), and this
    view is reachable from a button shown on *every* page, including to
    a session that's expired mid-visit — a clean redirect to login is
    worth the one extra line here even though the sibling views in this
    file share the same latent gap.

    Training mode (docs/UI.md "Training mode") — a single, focused page
    for actually being at the gym mid-workout: one exercise at a time,
    what's next, a rest timer, and the same smart suggestion the full
    session-detail page shows, without that page's full history/edit/
    delete chrome. Reachable from the floating button `base.html` shows
    on every page while a session is in progress (see
    apps.workouts.context_processors.active_workout_session).
    """
    session = get_object_or_404(
        services.sessions_for(request.user).select_related("workout").prefetch_related(
            "performed_exercises__exercise", "performed_exercises__sets"
        ),
        pk=pk,
    )
    if not session.is_in_progress:
        # Nothing left to train mid-workout once it's completed/abandoned
        # — the full detail page is the right place to review it.
        return redirect("workouts:session-detail", pk=session.pk)
    return render(request, "workouts/session_train.html", _train_context(request, session))


def _find_performed_exercise(performed_exercises, pk):
    if not pk:
        return None
    return next((pe for pe in performed_exercises if str(pe.pk) == str(pk)), None)


def _train_context(request, session, current=None):
    """Shared by the training-mode page and its HTMX set-log endpoint.

    `current` lets a caller pin a specific exercise (e.g. re-showing the
    same one after a validation error) instead of falling back to the
    `?pe=` query param or the auto-picked first incomplete exercise —
    see `session_train`/`train_set_log`.
    """
    performed_exercises = list(session.performed_exercises.all())
    if current is None:
        current = _find_performed_exercise(performed_exercises, request.GET.get("pe"))
    if current is None:
        current = services.first_incomplete_performed_exercise(performed_exercises)
    if current is None and performed_exercises:
        current = performed_exercises[-1]

    index = performed_exercises.index(current) if current in performed_exercises else -1
    set_form = suggestion = None
    if current is not None:
        set_form, suggestion = _build_set_form(request.user, current, session=session)

    return {
        "session": session,
        "performed_exercises": performed_exercises,
        "current": current,
        "index": index,
        "total": len(performed_exercises),
        "prev_pe": performed_exercises[index - 1] if index > 0 else None,
        "next_pe": (
            performed_exercises[index + 1] if 0 <= index < len(performed_exercises) - 1 else None
        ),
        "all_done": bool(performed_exercises)
        and services.first_incomplete_performed_exercise(performed_exercises) is None,
        "set_form": set_form,
        "suggestion": suggestion,
        "new_prs": [],
    }


@login_required
def train_set_log(request, performed_exercise_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    performed_exercise = _owned_performed_exercise_or_404(request, performed_exercise_pk)
    session = performed_exercise.session
    form = ExerciseSetForm(request.POST, user=request.user)
    if not session.is_in_progress:
        form.add_error(None, _("This session is no longer in progress."))
    logged = session.is_in_progress and form.is_valid()
    new_prs = []
    if logged:
        fields = dict(form.cleaned_data)
        fields["weight"] = core_units.display_to_kg(
            fields["weight"], getattr(request.user, "unit_system", "metric")
        )
        logged_set = services.log_set(performed_exercise, **fields)
        new_prs = records_services.check_and_record_prs(logged_set)

    if not request.headers.get("HX-Request"):
        # Same fallback set_log/_render_session_or_card use: without JS,
        # the browser does a plain form POST and expects a full page back
        # — this endpoint only ever renders the #train-panel *fragment*,
        # which has no <head>/stylesheet/nav of its own. A validation
        # error's details don't survive this redirect (same tradeoff
        # set_log already accepts for the same reason), but that's
        # strictly better than serving an unstyled, chromeless fragment
        # as if it were the whole page.
        _flash_new_prs(request, performed_exercise, new_prs)
        return redirect("workouts:session-train", pk=session.pk)

    if logged:
        # Stay on the same exercise if it still has sets left to log
        # (default_set_values will repeat this one as the next default);
        # otherwise hand off to whichever exercise is next incomplete —
        # the same auto-advance _train_context falls back to on a plain
        # page load, just computed right after the set that may have
        # just finished this one. `performed_exercise` here was fetched
        # without a sets prefetch, so `.sets.all()` (inside
        # is_performed_exercise_complete) queries fresh and already sees
        # the set just logged above.
        next_current = (
            performed_exercise
            if not services.is_performed_exercise_complete(performed_exercise)
            else None
        )
        context = _train_context(request, session, current=next_current)
    else:
        # Validation error (or session no longer in progress) — keep
        # showing the same exercise and the bound form with its errors,
        # rather than silently discarding what the user typed.
        context = _train_context(request, session, current=performed_exercise)
        context["set_form"] = form
    context["new_prs"] = new_prs
    response = render(request, "workouts/_train_panel.html", context)
    if logged:
        # Tells the rest-timer widget (base.html/session_train.html, kept
        # outside this HTMX swap target so a countdown in progress
        # survives it) to auto-start — but only on an actual successful
        # log, never on a validation error, which a generic
        # htmx:afterRequest listener couldn't tell apart from this (both
        # return 200 so the swap happens either way).
        response["HX-Trigger"] = json.dumps({"rest-timer-start": {"seconds": REST_SECONDS}})
    return response


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


def session_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    session = get_object_or_404(services.sessions_for(request.user), pk=pk)
    session.delete()
    return redirect("workouts:session-list")


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
    # `initial["weight"]` above is canonical kg (from stored history or
    # the progression engine) — ExerciseSetForm displays/accepts the
    # user's preferred unit, so it must be converted here before the
    # form ever renders it, the same as an instance's own weight is
    # converted inside the form itself.
    if initial.get("weight") is not None:
        unit_system = getattr(user, "unit_system", "metric")
        initial["weight"] = core_units.kg_to_display(initial["weight"], unit_system)
    return ExerciseSetForm(initial=initial, user=user), suggestion


def _flash_new_prs(request, performed_exercise, new_prs):
    """The no-JS fallback's only way to report a new PR — an HTMX
    request gets a toast instead (templates/records/_pr_toasts.html).
    Shared by set_log and train_set_log so the message text/conversion
    logic exists in exactly one place."""
    for record in new_prs:
        # records_services.format_value is the same conversion the
        # "Recent PRs" templates use, so an imperial-preference user
        # sees their own unit here too, not raw stored kg.
        messages.success(
            request,
            _("New PR — %(exercise)s: %(record_type)s %(value)s")
            % {
                "exercise": performed_exercise.exercise.name,
                "record_type": record.get_record_type_display(),
                "value": records_services.format_value(record, request.user),
            },
        )


def set_log(request, performed_exercise_pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    performed_exercise = _owned_performed_exercise_or_404(request, performed_exercise_pk)
    form = ExerciseSetForm(request.POST, user=request.user)
    new_prs = []
    suggestion = None
    if not performed_exercise.session.is_in_progress:
        form.add_error(None, _("This session is no longer in progress."))
    elif form.is_valid():
        # services.log_set takes cleaned_data directly rather than going
        # through form.save() (there's no ExerciseSet instance to save
        # onto yet — set_number is computed here), so the form's own
        # display-unit -> canonical-kg conversion (see ExerciseSetForm.save)
        # has to be repeated here rather than reused.
        fields = dict(form.cleaned_data)
        fields["weight"] = core_units.display_to_kg(
            fields["weight"], getattr(request.user, "unit_system", "metric")
        )
        logged_set = services.log_set(performed_exercise, **fields)
        new_prs = records_services.check_and_record_prs(logged_set)
        if not request.headers.get("HX-Request"):
            # Only for the no-JS fallback (a plain POST + redirect,
            # `_render_session_or_card` below) — an HTMX request instead
            # gets the same news via a toast (templates/records/
            # _pr_toasts.html, an out-of-band swap included from the
            # re-rendered card). Regression: this used to fire
            # unconditionally, including on every HTMX request, where
            # nothing ever consumes django.contrib.messages (only
            # base.html's full-page `{% if messages %}` loop does) — the
            # message sat in the store and would suddenly resurface,
            # stale, whenever the user's next *actual* full page load
            # happened to be, however much later and unrelated that was.
            _flash_new_prs(request, performed_exercise, new_prs)
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
        form = ExerciseSetForm(request.POST, instance=exercise_set, user=request.user)
        if form.is_valid():
            form.save()
            new_form, suggestion = _build_set_form(request.user, performed_exercise)
            return _render_session_or_card(
                request, performed_exercise, new_form, suggestion=suggestion
            )
    else:
        form = ExerciseSetForm(instance=exercise_set, user=request.user)
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
