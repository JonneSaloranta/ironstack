from itertools import groupby

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse
from django.shortcuts import redirect
from django.utils.translation import gettext as _
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from . import services
from .tours import TOURS


class TutorialListView(LoginRequiredMixin, TemplateView):
    """Profile → Tutorials: every tour, grouped by section, each with a
    "Start" button (runs it now) and "Show again" (re-arms it to start
    by itself next time), plus the on/off switch for automatic tours."""

    template_name = "tutorials/tutorial_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        done = services.completed_keys(user)
        rows = [
            {
                "tour": tour,
                "completed": tour.key in done,
                "start_url": (url := tour.url_for(user)) and f"{url}?tour={tour.key}",
            }
            for tour in TOURS
        ]
        context["sections"] = [
            (section, list(items)) for section, items in groupby(rows, lambda r: r["tour"].section)
        ]
        context["any_completed"] = bool(done)
        return context


def _tour_or_404(key):
    tour = services.tour_by_key(key)
    if tour is None:
        raise Http404
    return tour


@login_required
@require_POST
def complete(request, key):
    """Called by static/js/tutorial.js when a tour is finished or skipped."""
    services.mark_completed(request.user, _tour_or_404(key).key)
    return HttpResponse(status=204)


@login_required
@require_POST
def disable(request):
    """"Don't show tutorials" in a running tour: no tour starts by itself
    from now on (they can still be started from profile → Tutorials)."""
    request.user.tutorials_enabled = False
    request.user.save(update_fields=["tutorials_enabled"])
    return HttpResponse(status=204)


@login_required
@require_POST
def tutorial_settings(request):
    request.user.tutorials_enabled = request.POST.get("enabled") == "on"
    request.user.save(update_fields=["tutorials_enabled"])
    messages.success(request, _("Tutorial settings saved."))
    return redirect("tutorials:list")


@login_required
@require_POST
def reset_one(request, key):
    tour = _tour_or_404(key)
    services.reset(request.user, tour.key)
    message = _("“%(title)s” will start again the next time you open it.")
    messages.success(request, message % {"title": tour.title})
    return redirect("tutorials:list")


@login_required
@require_POST
def reset_all(request):
    services.reset(request.user)
    messages.success(request, _("Every tutorial will start again the next time you open its page."))
    return redirect("tutorials:list")
