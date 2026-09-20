from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from . import services
from .forms import ExerciseForm, ExerciseImageForm
from .models import Exercise, ExerciseImage, ExerciseImageSettings, MuscleGroup


class ExerciseListView(LoginRequiredMixin, ListView):
    model = Exercise
    template_name = "exercises/exercise_list.html"
    context_object_name = "exercises"
    paginate_by = 30

    def get_queryset(self):
        qs = services.visible_to(self.request.user).prefetch_related(
            "primary_muscle_groups"
        )
        query = self.request.GET.get("q", "").strip()
        if query:
            qs = services.search(qs, query)
        muscle_group = self.request.GET.get("muscle_group", "").strip()
        if muscle_group:
            qs = qs.filter(primary_muscle_groups__id=muscle_group)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["muscle_groups"] = MuscleGroup.objects.all()
        context["query"] = self.request.GET.get("q", "")
        context["selected_muscle_group"] = self.request.GET.get("muscle_group", "")
        return context

    def get_template_names(self):
        if self.request.headers.get("HX-Request"):
            return ["exercises/_exercise_list_results.html"]
        return [self.template_name]


class ExerciseDetailView(LoginRequiredMixin, DetailView):
    model = Exercise
    template_name = "exercises/exercise_detail.html"
    context_object_name = "exercise"

    def get_queryset(self):
        return services.visible_to(self.request.user, include_inactive=True).prefetch_related(
            "images"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["max_images_per_exercise"] = ExerciseImageSettings.load().max_images_per_exercise
        if self.object.owner_id == self.request.user.id:
            context["image_form"] = ExerciseImageForm()
        return context


class ExerciseCreateView(LoginRequiredMixin, CreateView):
    model = Exercise
    form_class = ExerciseForm
    template_name = "exercises/exercise_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        response = super().form_valid(form)
        messages.success(self.request, _("Exercise created."))
        return response

    def get_success_url(self):
        return reverse_lazy("exercises:exercise-detail", args=[self.object.pk])


class ExerciseUpdateView(LoginRequiredMixin, UpdateView):
    model = Exercise
    form_class = ExerciseForm
    template_name = "exercises/exercise_form.html"

    def get_queryset(self):
        # Only a user's own custom exercises can be edited — system
        # exercises are managed via the admin, not user-facing views.
        return Exercise.objects.filter(owner=self.request.user)

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Exercise updated."))
        return response

    def get_success_url(self):
        return reverse_lazy("exercises:exercise-detail", args=[self.object.pk])


@login_required
def exercise_deactivate(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    exercise = get_object_or_404(Exercise, pk=pk, owner=request.user)
    exercise.active = False
    exercise.save(update_fields=["active"])
    messages.success(request, _("Exercise deactivated."))
    return redirect("exercises:exercise-list")


@login_required
def exercise_image_create(request, pk):
    """Adds one image to a user's own custom exercise (system exercises'
    images are admin-only, same as every other edit to them — see
    ExerciseUpdateView's own docstring). Redirects back to the detail
    page either way rather than re-rendering a dedicated upload page —
    there's no separate page to re-render, the upload form lives
    directly on the exercise detail page next to the gallery it feeds.
    """
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    exercise = get_object_or_404(Exercise, pk=pk, owner=request.user)
    form = ExerciseImageForm(
        request.POST, request.FILES, instance=ExerciseImage(exercise=exercise)
    )
    if form.is_valid():
        form.save()
        messages.success(request, _("Image added."))
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
    return redirect("exercises:exercise-detail", pk=exercise.pk)


@login_required
def exercise_image_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    image = get_object_or_404(ExerciseImage, pk=pk, exercise__owner=request.user)
    exercise_pk = image.exercise_id
    image.image.delete(save=False)
    image.delete()
    messages.success(request, _("Image removed."))
    return redirect("exercises:exercise-detail", pk=exercise_pk)
