from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from . import services
from .forms import ExerciseForm
from .models import Exercise, MuscleGroup


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
            qs = qs.filter(name__icontains=query)
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
        return services.visible_to(self.request.user, include_inactive=True)


class ExerciseCreateView(LoginRequiredMixin, CreateView):
    model = Exercise
    form_class = ExerciseForm
    template_name = "exercises/exercise_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

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

    def get_success_url(self):
        return reverse_lazy("exercises:exercise-detail", args=[self.object.pk])


def exercise_deactivate(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    exercise = get_object_or_404(Exercise, pk=pk, owner=request.user)
    exercise.active = False
    exercise.save(update_fields=["active"])
    return redirect("exercises:exercise-list")
