from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import HttpResponseNotAllowed, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.text import slugify
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

from apps.core import version as version_info
from apps.core.data_exchange import ImportValidationError, build_envelope, parse_envelope
from apps.core.forms import ExportImportUploadForm

from . import services
from .forms import ExercisePrescriptionForm, ProgramForm, WorkoutForm
from .models import ExercisePrescription, Program, Workout


class ProgramListView(LoginRequiredMixin, ListView):
    template_name = "programs/program_list.html"
    context_object_name = "programs"

    def get_queryset(self):
        # Annotated once here rather than `program.workouts.count` in the
        # template, which would issue its own COUNT query per row (and
        # twice per row at that, once for the number and once for
        # |pluralize) instead of one query for the whole list.
        return services.editable_by(self.request.user).annotate(workout_count=Count("workouts"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["templates"] = Program.objects.filter(
            owner__isnull=True, is_template=True
        ).annotate(workout_count=Count("workouts"))
        # A plain per-object Python attribute, not a queryset
        # annotation — apps.coaching.services.program_update_available
        # is one cheap int compare per coach-imported program, and this
        # list is always just one user's own programs (never hundreds,
        # unlike the workout_count above where an N+1 actually
        # mattered), so a small extra query per imported program here
        # is not worth a bespoke ORM expression to avoid.
        from apps.coaching.services import program_update_available

        for program in context["programs"]:
            program.has_coach_update = (
                program.imported_from_id is not None and program_update_available(program)
            )
        # Split the same flat queryset into the groups
        # templates/programs/program_list.html actually renders —
        # asked for directly: a coach-imported program is visually
        # distinct from a program the user built themselves, which is
        # itself distinct from their own saved templates. A program
        # imported from a coach sorts there regardless of is_template
        # (checked first) — that combination shouldn't happen in
        # practice, but if it ever did, "where did this come from" is
        # more useful here than "is it a template".
        context["coach_programs"] = [
            program for program in context["programs"] if program.imported_from_id is not None
        ]
        context["own_templates"] = [
            program
            for program in context["programs"]
            if program.imported_from_id is None and program.is_template
        ]
        context["own_programs"] = [
            program
            for program in context["programs"]
            if program.imported_from_id is None and not program.is_template
        ]
        return context


class ProgramDetailView(LoginRequiredMixin, DetailView):
    template_name = "programs/program_detail.html"
    context_object_name = "program"

    def get_queryset(self):
        return services.visible_to(self.request.user).prefetch_related(
            "workouts__prescriptions__exercise"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["can_edit"] = self.object.owner_id == self.request.user.id
        return context


class ProgramCreateView(LoginRequiredMixin, CreateView):
    model = Program
    form_class = ProgramForm
    template_name = "programs/program_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, _("Program created."))
        return response

    def get_success_url(self):
        return reverse("programs:program-detail", args=[self.object.pk])


class ProgramUpdateView(LoginRequiredMixin, UpdateView):
    model = Program
    form_class = ProgramForm
    template_name = "programs/program_form.html"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def get_queryset(self):
        return services.editable_by(self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        self.object.bump_version()
        messages.success(self.request, _("Program saved."))
        return response

    def get_success_url(self):
        return reverse("programs:program-detail", args=[self.object.pk])


class ProgramDeleteView(LoginRequiredMixin, DeleteView):
    model = Program
    template_name = "programs/program_confirm_delete.html"

    def get_queryset(self):
        return services.editable_by(self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Program deleted."))
        return response

    def get_success_url(self):
        return reverse("programs:program-list")


@login_required
def program_copy(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    source = get_object_or_404(services.visible_to(request.user), pk=pk)
    new_program = services.copy_program(source, owner=request.user)
    return redirect("programs:program-detail", pk=new_program.pk)


@login_required
def program_export(request, pk):
    """Downloads one program (visible to this user — their own, or a
    system template) as a plain, human-readable `.json` file — see
    apps.core.data_exchange's own module docstring for the whole
    export/import feature this is one half of. A GET, not a POST:
    downloading a file changes nothing server-side, so there's no
    action here to protect against a stray re-request the way
    program_copy's own POST-only guard does."""
    program = get_object_or_404(services.visible_to(request.user), pk=pk)
    envelope = build_envelope("program", services.export_program(program))
    response = JsonResponse(envelope, json_dumps_params={"indent": 2, "ensure_ascii": False})
    filename = f"ironstack-program-{slugify(program.name) or program.pk}.json"
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


@login_required
def program_import(request):
    """The other half — uploads a previously-exported program (asked
    for directly: this instance's own, or a completely different
    IronStack instance's) and recreates it as a new program owned by
    the current user. `apps.programs.services.import_program` runs the
    whole thing inside one transaction, so a bad file (or one this
    build genuinely can't make sense of) leaves no trace rather than a
    half-imported program — see that function's own docstring."""
    if request.method != "POST":
        return render(request, "programs/program_import.html", {"form": ExportImportUploadForm()})

    form = ExportImportUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, "programs/program_import.html", {"form": form})

    try:
        payload, exported_app_version = parse_envelope(
            form.cleaned_data["export_file"].read(), expected_kind="program"
        )
        program = services.import_program(request.user, payload)
    except ImportValidationError as exc:
        form.add_error("export_file", str(exc))
        return render(request, "programs/program_import.html", {"form": form})

    messages.success(request, _("Imported “%(name)s”.") % {"name": program.name})
    if exported_app_version != version_info.get_version():
        messages.info(
            request,
            _(
                "This file was exported from IronStack %(version)s — worth "
                "double-checking the import if something looks off."
            )
            % {"version": exported_app_version},
        )
    return redirect("programs:program-detail", pk=program.pk)


def _owned_program_or_404(request, program_pk):
    return get_object_or_404(services.editable_by(request.user), pk=program_pk)


@login_required
def workout_create(request, program_pk):
    program = _owned_program_or_404(request, program_pk)
    form = WorkoutForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        workout = form.save(commit=False)
        workout.program = program
        workout.save()
        program.bump_version()
        messages.success(request, _("Workout added."))
        return redirect("programs:program-detail", pk=program.pk)
    return _render_program_form(
        request, "programs/workout_form.html", {"form": form, "program": program}
    )


@login_required
def workout_update(request, program_pk, pk):
    program = _owned_program_or_404(request, program_pk)
    workout = get_object_or_404(Workout, pk=pk, program=program)
    form = WorkoutForm(request.POST or None, instance=workout)
    if request.method == "POST" and form.is_valid():
        form.save()
        program.bump_version()
        messages.success(request, _("Workout saved."))
        return redirect("programs:program-detail", pk=program.pk)
    return _render_program_form(
        request, "programs/workout_form.html", {"form": form, "program": program}
    )


@login_required
def workout_delete(request, program_pk, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    program = _owned_program_or_404(request, program_pk)
    workout = get_object_or_404(Workout, pk=pk, program=program)
    workout.delete()
    program.bump_version()
    messages.success(request, _("Workout deleted."))
    return redirect("programs:program-detail", pk=program.pk)


@login_required
def prescription_create(request, program_pk, workout_pk):
    program = _owned_program_or_404(request, program_pk)
    workout = get_object_or_404(Workout, pk=workout_pk, program=program)
    form = ExercisePrescriptionForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        prescription = form.save(commit=False)
        prescription.workout = workout
        prescription.save()
        program.bump_version()
        messages.success(request, _("Exercise added."))
        return redirect("programs:program-detail", pk=program.pk)
    return _render_program_form(
        request,
        "programs/prescription_form.html",
        {"form": form, "program": program, "workout": workout},
    )


@login_required
def prescription_update(request, program_pk, workout_pk, pk):
    program = _owned_program_or_404(request, program_pk)
    workout = get_object_or_404(Workout, pk=workout_pk, program=program)
    prescription = get_object_or_404(ExercisePrescription, pk=pk, workout=workout)
    form = ExercisePrescriptionForm(
        request.POST or None, instance=prescription, user=request.user
    )
    if request.method == "POST" and form.is_valid():
        form.save()
        program.bump_version()
        messages.success(request, _("Exercise saved."))
        return redirect("programs:program-detail", pk=program.pk)
    return _render_program_form(
        request,
        "programs/prescription_form.html",
        {"form": form, "program": program, "workout": workout},
    )


@login_required
def prescription_delete(request, program_pk, workout_pk, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    program = _owned_program_or_404(request, program_pk)
    workout = get_object_or_404(Workout, pk=workout_pk, program=program)
    prescription = get_object_or_404(ExercisePrescription, pk=pk, workout=workout)
    prescription.delete()
    program.bump_version()
    messages.success(request, _("Exercise removed."))
    return redirect("programs:program-detail", pk=program.pk)


def _render_program_form(request, template_name, context):
    return render(request, template_name, context)
