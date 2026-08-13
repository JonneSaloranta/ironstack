import dataclasses

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView

from . import services, units
from .forms import ActivityForm, ActivityTypeForm
from .models import Activity, ActivityType


class ActivityTypeListView(LoginRequiredMixin, ListView):
    template_name = "activities/activity_type_list.html"
    context_object_name = "activity_types"

    def get_queryset(self):
        # The list page only needs a count per type — annotate it in one
        # query rather than materializing every logged Activity row per
        # type via services.summarize() (which the history page needs in
        # full, but the list page doesn't). Filtered on this user
        # explicitly: activity_type may be a system type shared by every
        # user, so an unfiltered Count would leak other users' totals.
        user = self.request.user
        return services.visible_to(user).annotate(
            entry_count=Count("activities", filter=Q(activities__user=user))
        )


class ActivityTypeCreateView(LoginRequiredMixin, CreateView):
    model = ActivityType
    form_class = ActivityTypeForm
    template_name = "activities/activity_type_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("activities:history", args=[self.object.pk])


class ActivityHistoryView(LoginRequiredMixin, DetailView):
    template_name = "activities/activity_history.html"
    context_object_name = "activity_type"

    def get_queryset(self):
        return services.visible_to(self.request.user, include_inactive=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        activity_type = self.object
        history = list(services.history_for(user, activity_type))
        distance_label = units.distance_unit_label(user.unit_system)
        for entry in history:
            entry.display_distance = units.distance_to_display(entry.distance, user.unit_system)
        context["history"] = history
        # summarize() totals canonical meters (it doesn't know about
        # display units) — convert the one distance-shaped field before
        # it reaches the template, same as each entry's own distance.
        summary = services.summarize(history)
        if summary.total_distance is not None:
            summary = dataclasses.replace(
                summary,
                total_distance=units.distance_to_display(
                    summary.total_distance, user.unit_system
                ),
            )
        context["summary"] = summary
        context["distance_label"] = distance_label
        context["chart"] = services.duration_chart_series(history)
        context["form"] = ActivityForm(user=user, activity_type=activity_type)
        context["can_deactivate"] = activity_type.owner_id == user.id
        return context


def activity_log(request, type_pk):
    activity_type = get_object_or_404(services.visible_to(request.user), pk=type_pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = ActivityForm(request.POST, user=request.user, activity_type=activity_type)
    if form.is_valid():
        form.save()
    return redirect("activities:history", pk=activity_type.pk)


def _owned_activity_or_404(request, pk):
    return get_object_or_404(Activity, pk=pk, user=request.user)


def activity_edit(request, pk):
    activity = _owned_activity_or_404(request, pk)
    if request.method == "POST":
        form = ActivityForm(
            request.POST, instance=activity, user=request.user, activity_type=activity.activity_type
        )
        if form.is_valid():
            form.save()
            return redirect("activities:history", pk=activity.activity_type_id)
    else:
        form = ActivityForm(
            instance=activity, user=request.user, activity_type=activity.activity_type
        )
    return render(
        request,
        "activities/activity_form.html",
        {"form": form, "activity_type": activity.activity_type},
    )


def activity_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    activity = _owned_activity_or_404(request, pk)
    type_pk = activity.activity_type_id
    activity.delete()
    return redirect("activities:history", pk=type_pk)


def activity_type_deactivate(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    activity_type = get_object_or_404(ActivityType, pk=pk, owner=request.user)
    activity_type.active = False
    activity_type.save(update_fields=["active"])
    return redirect("activities:type-list")
