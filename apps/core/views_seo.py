"""Profile → Administration → Site & SEO (staff only) — see
apps.core.models.SeoSettings for what the one toggle here actually
does once saved, and apps.core.context_processors.seo/apps.core.
views.robots_txt for where it's actually read."""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils.translation import gettext as _
from django.views.generic import View

from apps.core.forms import SeoSettingsForm
from apps.core.mixins import StaffRequiredMixin
from apps.core.models import SeoSettings


class SeoSettingsView(StaffRequiredMixin, View):
    template_name = "core/seo_settings.html"

    def get(self, request):
        form = SeoSettingsForm(instance=SeoSettings.load())
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = SeoSettingsForm(request.POST, instance=SeoSettings.load())
        if form.is_valid():
            form.save()
            messages.success(request, _("SEO settings saved."))
            return redirect("seo-settings")
        return render(request, self.template_name, {"form": form})
