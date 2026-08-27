from datetime import timedelta

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse
from django.utils import timezone
from django.utils.formats import date_format
from django.utils.translation import gettext as _
from django.views.generic import TemplateView

from apps.analytics import achievements as achievement_services
from apps.analytics import dateranges
from apps.analytics import services as analytics_services
from apps.core import greetings as greeting_services
from apps.core import units as core_units
from apps.core.models import SeoSettings
from apps.measurements import services as measurement_services
from apps.measurements import units as measurement_units
from apps.measurements.models import MeasurementType
from apps.nutrition import services as nutrition_services
from apps.nutrition.models import NutritionTarget
from apps.workouts.models import WorkoutSessionStatus
from apps.workouts.services import sessions_for


def _day_detail_lines(status, target_calories):
    """The two short, plain-language lines a tap on a calendar day
    reveals (templates/nutrition/_month_calendar.html's popover) — the
    one place in this calendar actual text appears, since the grid
    itself is deliberately icon/color-only."""
    if status.training_status == "pr":
        training_line = _("Training day — new personal record!")
    elif status.training_status == "abandoned":
        training_line = _("Training day — a session was abandoned.")
    elif status.training_status == "completed":
        training_line = _("Training day.")
    else:
        training_line = _("Rest day.")

    if status.actual_calories is None:
        calorie_line = _("Nothing logged that day.")
    elif target_calories is not None:
        calorie_line = _("%(actual)s / %(target)s kcal logged.") % {
            "actual": int(status.actual_calories),
            "target": target_calories,
        }
    else:
        calorie_line = _("%(actual)s kcal logged.") % {"actual": int(status.actual_calories)}

    return [str(training_line), str(calorie_line)]


def _month_calendar_context(request, today):
    """The dashboard's month calendar (templates/nutrition/
    _month_calendar.html) — one real month at a time, browsable to any
    earlier *or later* one via `?month=YYYY-MM`. `weeks` is a list of
    7-day rows, Monday first (matching apps.programs.Weekday's own
    numbering elsewhere in this app), each day a dict the template can
    render without any further lookups. `calendar_days_json` is the
    same days' data again, shaped for the tap-a-day detail popover (a
    JS component, not more Django template — see that partial's own
    comment) via `|json_script`."""
    import calendar as calendar_module
    from datetime import date as date_cls
    from datetime import timedelta

    requested = request.GET.get("month", "")
    try:
        year, month = (int(part) for part in requested.split("-", 1))
        first_of_requested = date_cls(year, month, 1)
    except (ValueError, TypeError):
        first_of_requested = date_cls(today.year, today.month, 1)
    year, month = first_of_requested.year, first_of_requested.month

    target = NutritionTarget.objects.filter(user=request.user, ended_at__isnull=True).first()
    target_calories = target.daily_calories if target is not None else None
    all_statuses = nutrition_services.calendar_month_statuses(request.user, year, month)
    statuses_by_date = {status.date: status for status in all_statuses}
    calendar_days_json = [
        {
            "date": status.date.isoformat(),
            "heading": date_format(status.date, format="SHORT_DATE_FORMAT", use_l10n=True),
            "lines": _day_detail_lines(status, target_calories),
        }
        for status in all_statuses
    ]

    weeks = []
    for week in calendar_module.Calendar(firstweekday=0).monthdatescalendar(year, month):
        weeks.append(
            [
                {
                    "date": day,
                    "in_month": day.month == month,
                    "is_today": day == today,
                    "status": statuses_by_date.get(day),
                }
                for day in week
            ]
        )

    prev_month = first_of_requested - timedelta(days=1)
    next_month = first_of_requested + timedelta(days=32)
    return {
        "calendar_month": first_of_requested,
        "calendar_weeks": weeks,
        "calendar_days_json": calendar_days_json,
        "calendar_prev_month": prev_month.strftime("%Y-%m"),
        "calendar_next_month": next_month.strftime("%Y-%m"),
    }


class DashboardView(LoginRequiredMixin, TemplateView):
    """docs/UI.md "Dashboard — Possible content": next/last workout (the
    in-progress banner below), this week's volume, recent PRs, body
    weight, recent activity (that last one is left to its own section —
    apps.activities already has a dedicated, working history per type;
    duplicating it here would just be a second, staler copy)."""

    template_name = "core/dashboard.html"

    def get_template_names(self):
        # Changing month is an HTMX request (templates/nutrition/
        # _month_calendar.html's own prev/next links and out-of-month
        # day cells) swapping just that one card, not a full page
        # navigation — so only that partial needs rendering, not
        # every other section's own query below (recent PRs, weight,
        # achievements, ...) none of which changed at all.
        if self.request.headers.get("HX-Request"):
            return ["nutrition/_month_calendar.html"]
        return [self.template_name]

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        today = timezone.localdate()
        context.update(_month_calendar_context(self.request, today))
        if self.request.headers.get("HX-Request"):
            return context

        user = self.request.user
        context["greeting"] = greeting_services.random_greeting(user)
        context["in_progress_session"] = (
            sessions_for(user).filter(status=WorkoutSessionStatus.IN_PROGRESS).first()
        )
        this_week = dateranges.resolve(None, start=today - timedelta(days=today.weekday()))
        context["week_summary"] = analytics_services.training_summary(user, this_week)
        context["recent_prs"] = analytics_services.pr_history(
            user, dateranges.resolve("30d"), limit=3
        )
        context["weight_unit_label"] = core_units.weight_unit_label(user.unit_system)

        body_weight_type = MeasurementType.objects.filter(name="Body weight", owner=None).first()
        if body_weight_type:
            latest = measurement_services.latest_for(user, body_weight_type)
            context["body_weight"] = (
                measurement_units.to_display(
                    latest.value, body_weight_type.unit_kind, user.unit_system
                )
                if latest
                else None
            )
            context["body_weight_unit"] = measurement_units.display_unit_label(
                body_weight_type.unit_kind, user.unit_system
            )

        # Shared across every user on this instance, not scoped to
        # `user` — apps.analytics.achievements.achievement_highlights
        # already excludes anyone with show_achievements=False (a
        # privacy opt-out of being *included*, not a personal "hide the
        # carousel from me" toggle — see that function's docstring), so
        # there's nothing left to gate here.
        context["achievements"] = achievement_services.achievement_highlights()
        context["recently_active"] = achievement_services.recently_active_users()
        return context


def healthcheck(request):
    """Unauthenticated liveness endpoint for Docker/reverse-proxy checks."""
    return HttpResponse("ok")


def _serve_static_root_file(filename, content_type):
    """Serve a file from the `static/` source directory at the *site
    root* rather than under `/static/`. A service worker's default scope
    is the directory it's served from — `/sw.js` covers the whole app,
    `/static/sw.js` would only ever cover `/static/`, which is useless.
    Reads the source file directly (not STATIC_ROOT), so this works
    whether or not `collectstatic` has run yet.
    """
    path = settings.BASE_DIR / "static" / filename
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        raise Http404 from None
    response = HttpResponse(content, content_type=content_type)
    if filename == "sw.js":
        response["Service-Worker-Allowed"] = "/"
    return response


def service_worker(request):
    return _serve_static_root_file("sw.js", "application/javascript")


def web_manifest(request):
    return _serve_static_root_file("manifest.json", "application/manifest+json")


def robots_txt(request):
    """Generated, not a static file — its whole content depends on
    apps.core.models.SeoSettings, which an operator can flip from
    Profile → Administration → Site & SEO without a redeploy. `<meta
    name="robots">` (base.html, apps.core.context_processors.seo) is
    the second, more universally-respected half of the same control —
    robots.txt itself is only ever advisory, a well-behaved crawler's
    own choice to honor."""
    if SeoSettings.load().search_engine_indexing_enabled:
        body = "User-agent: *\nAllow: /\n"
    else:
        body = "User-agent: *\nDisallow: /\n"
    return HttpResponse(body, content_type="text/plain")
