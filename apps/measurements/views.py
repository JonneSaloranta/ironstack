from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import CreateView, DetailView, ListView

from apps.core import bmi as bmi_services
from apps.core import units as core_units

from . import services, units
from .forms import BodyMeasurementForm, MeasurementTypeForm
from .models import BodyMeasurement, MeasurementType


class MeasurementTypeListView(LoginRequiredMixin, ListView):
    template_name = "measurements/measurement_type_list.html"
    context_object_name = "measurement_types"

    def get_queryset(self):
        return services.visible_to(self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        for measurement_type in context["measurement_types"]:
            latest = services.latest_for(user, measurement_type)
            measurement_type.latest_display = (
                units.to_display(latest.value, measurement_type.unit_kind, user.unit_system)
                if latest
                else None
            )
            measurement_type.unit_label = units.display_unit_label(
                measurement_type.unit_kind, user.unit_system
            )
        return context


class MeasurementTypeCreateView(LoginRequiredMixin, CreateView):
    model = MeasurementType
    form_class = MeasurementTypeForm
    template_name = "measurements/measurement_type_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("measurements:history", args=[self.object.pk])


class MeasurementHistoryView(LoginRequiredMixin, DetailView):
    template_name = "measurements/measurement_history.html"
    context_object_name = "measurement_type"

    def get_queryset(self):
        return services.visible_to(self.request.user, include_inactive=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        measurement_type = self.object
        history = list(services.history_for(user, measurement_type))
        unit_label = units.display_unit_label(measurement_type.unit_kind, user.unit_system)
        for entry in history:
            entry.display_value = units.to_display(
                entry.value, measurement_type.unit_kind, user.unit_system
            )
        context["history"] = history
        context["unit_label"] = unit_label
        # The chart plots display units (what the user actually reads),
        # not canonical storage — same converted values as the table.
        chart_points = [(entry.display_value, entry.recorded_at) for entry in history]
        context["chart"] = services.build_chart_series(chart_points)
        context["form"] = BodyMeasurementForm(user=user, measurement_type=measurement_type)
        context["can_deactivate"] = measurement_type.owner_id == user.id

        # BMI lives here, not on the dashboard/profile — it only ever
        # meant anything alongside a *logged body weight*, and this is
        # the page that logs one. Gated the same way it always was:
        # `show_bmi` (a personal display toggle) and specifically the
        # system "Body weight" type, since BMI has nothing to say about
        # any other measurement (waist, body fat %, ...).
        is_body_weight_type = (
            measurement_type.name == "Body weight" and measurement_type.owner_id is None
        )
        if user.show_bmi and is_body_weight_type:
            context["bmi_category_rows"] = bmi_services.category_rows(
                user.height, user.unit_system
            )
            context["weight_unit_label"] = core_units.weight_unit_label(user.unit_system)
            if user.height and history:
                bmi = bmi_services.calculate_bmi(history[0].value, user.height)
                if bmi is not None:
                    context["bmi"] = bmi
                    context["bmi_category"] = bmi_services.category_for(bmi)
        return context


@login_required
def measurement_log(request, type_pk):
    measurement_type = get_object_or_404(
        services.visible_to(request.user), pk=type_pk
    )
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = BodyMeasurementForm(
        request.POST, user=request.user, measurement_type=measurement_type
    )
    if form.is_valid():
        form.save()
    return redirect("measurements:history", pk=measurement_type.pk)


def _owned_measurement_or_404(request, pk):
    return get_object_or_404(BodyMeasurement, pk=pk, user=request.user)


@login_required
def measurement_edit(request, pk):
    measurement = _owned_measurement_or_404(request, pk)
    if request.method == "POST":
        form = BodyMeasurementForm(
            request.POST,
            instance=measurement,
            user=request.user,
            measurement_type=measurement.measurement_type,
        )
        if form.is_valid():
            form.save()
            return redirect("measurements:history", pk=measurement.measurement_type_id)
    else:
        form = BodyMeasurementForm(
            instance=measurement, user=request.user, measurement_type=measurement.measurement_type
        )
    return render(
        request,
        "measurements/measurement_form.html",
        {"form": form, "measurement_type": measurement.measurement_type},
    )


@login_required
def measurement_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    measurement = _owned_measurement_or_404(request, pk)
    type_pk = measurement.measurement_type_id
    measurement.delete()
    return redirect("measurements:history", pk=type_pk)


@login_required
def measurement_type_deactivate(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    measurement_type = get_object_or_404(MeasurementType, pk=pk, owner=request.user)
    measurement_type.active = False
    measurement_type.save(update_fields=["active"])
    return redirect("measurements:type-list")
