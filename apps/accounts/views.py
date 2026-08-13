from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from django.views.generic import CreateView, UpdateView

from apps.core import bmi as bmi_services
from apps.core import units as core_units
from apps.measurements import services as measurement_services
from apps.measurements.models import MeasurementType

from .forms import ProfileForm, SignupForm


class SignupView(CreateView):
    form_class = SignupForm
    template_name = "accounts/signup.html"
    success_url = reverse_lazy("dashboard")

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


class ProfileView(LoginRequiredMixin, UpdateView):
    form_class = ProfileForm
    template_name = "accounts/profile.html"
    success_url = reverse_lazy("profile")

    def get_object(self):
        return self.request.user

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        # The BMI category ranges are shown here unconditionally — not
        # just on the dashboard's BMI card, which only ever appears once
        # a height *and* a logged body weight both exist. This is the
        # page height itself lives on, so the ranges (and the current
        # value, once computable) need to be findable here regardless of
        # whether that dashboard card has ever had reason to render.
        user = self.request.user
        context["bmi_category_rows"] = bmi_services.category_rows(
            user.height, user.unit_system
        )
        context["weight_unit_label"] = core_units.weight_unit_label(user.unit_system)
        if user.show_bmi and user.height:
            body_weight_type = MeasurementType.objects.filter(
                name="Body weight", owner=None
            ).first()
            latest = (
                measurement_services.latest_for(user, body_weight_type)
                if body_weight_type
                else None
            )
            if latest:
                bmi = bmi_services.calculate_bmi(latest.value, user.height)
                if bmi is not None:
                    context["bmi"] = bmi
                    context["bmi_category"] = bmi_services.category_for(bmi)
        return context

    def form_valid(self, form):
        response = super().form_valid(form)
        messages.success(self.request, _("Preferences saved."))
        return response
