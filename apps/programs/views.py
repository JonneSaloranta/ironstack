from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import CreateView, DeleteView, DetailView, ListView, UpdateView

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

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("programs:program-detail", args=[self.object.pk])


class ProgramUpdateView(LoginRequiredMixin, UpdateView):
    model = Program
    form_class = ProgramForm
    template_name = "programs/program_form.html"

    def get_queryset(self):
        return services.editable_by(self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        self.object.bump_version()
        return response

    def get_success_url(self):
        return reverse("programs:program-detail", args=[self.object.pk])


class ProgramDeleteView(LoginRequiredMixin, DeleteView):
    model = Program
    template_name = "programs/program_confirm_delete.html"

    def get_queryset(self):
        return services.editable_by(self.request.user)

    def get_success_url(self):
        return reverse("programs:program-list")


def program_copy(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    source = get_object_or_404(services.visible_to(request.user), pk=pk)
    new_program = services.copy_program(source, owner=request.user)
    return redirect("programs:program-detail", pk=new_program.pk)


def _owned_program_or_404(request, program_pk):
    return get_object_or_404(services.editable_by(request.user), pk=program_pk)


def workout_create(request, program_pk):
    program = _owned_program_or_404(request, program_pk)
    form = WorkoutForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        workout = form.save(commit=False)
        workout.program = program
        workout.save()
        program.bump_version()
        return redirect("programs:program-detail", pk=program.pk)
    return _render_program_form(
        request, "programs/workout_form.html", {"form": form, "program": program}
    )


def workout_update(request, program_pk, pk):
    program = _owned_program_or_404(request, program_pk)
    workout = get_object_or_404(Workout, pk=pk, program=program)
    form = WorkoutForm(request.POST or None, instance=workout)
    if request.method == "POST" and form.is_valid():
        form.save()
        program.bump_version()
        return redirect("programs:program-detail", pk=program.pk)
    return _render_program_form(
        request, "programs/workout_form.html", {"form": form, "program": program}
    )


def workout_delete(request, program_pk, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    program = _owned_program_or_404(request, program_pk)
    workout = get_object_or_404(Workout, pk=pk, program=program)
    workout.delete()
    program.bump_version()
    return redirect("programs:program-detail", pk=program.pk)


def prescription_create(request, program_pk, workout_pk):
    program = _owned_program_or_404(request, program_pk)
    workout = get_object_or_404(Workout, pk=workout_pk, program=program)
    form = ExercisePrescriptionForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        prescription = form.save(commit=False)
        prescription.workout = workout
        prescription.save()
        program.bump_version()
        return redirect("programs:program-detail", pk=program.pk)
    return _render_program_form(
        request,
        "programs/prescription_form.html",
        {"form": form, "program": program, "workout": workout},
    )


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
        return redirect("programs:program-detail", pk=program.pk)
    return _render_program_form(
        request,
        "programs/prescription_form.html",
        {"form": form, "program": program, "workout": workout},
    )


def prescription_delete(request, program_pk, workout_pk, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    program = _owned_program_or_404(request, program_pk)
    workout = get_object_or_404(Workout, pk=workout_pk, program=program)
    prescription = get_object_or_404(ExercisePrescription, pk=pk, workout=workout)
    prescription.delete()
    program.bump_version()
    return redirect("programs:program-detail", pk=program.pk)


def _render_program_form(request, template_name, context):
    return render(request, template_name, context)
