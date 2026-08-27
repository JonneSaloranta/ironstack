"""The nutrition onboarding wizard — see docs/NUTRITION.md "Phased
implementation plan" step 3. Five small steps (spec: step-by-step, not
one giant form), state accumulated in the session between them
(`request.session["nutrition_onboarding"]`, Decimal/date values kept
as plain strings since the session is JSON-serialized) and committed
atomically only on the last step's POST.
"""

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Max, Prefetch
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext as _
from django.utils.translation import ngettext
from django.views.generic import CreateView, ListView, View

from apps.measurements.models import BodyMeasurement, MeasurementType
from apps.programs.models import Weekday as ProgramsWeekday

from . import diet_builder, energy, services
from .forms import (
    DEFAULT_RATES_JSON_SAFE,
    ActivityInputsForm,
    ActivityLevelConfirmForm,
    BMICalculatorForm,
    BmrTdeeCalculatorForm,
    BodyFatCalculatorForm,
    BodyStepForm,
    DiaryAddEntryForm,
    DiaryEntryQuantityForm,
    DietPlanForm,
    DietPlanItemForm,
    DietPlanMealItemSearchForm,
    FoodForm,
    GoalStepForm,
    LogDietPlanForm,
    LogRecipeForm,
    MacroCalculatorForm,
    RecipeForm,
    RecipeIngredientQuantityForm,
    RecipeIngredientSearchForm,
    TimeToGoalCalculatorForm,
    WaistHipRatioCalculatorForm,
    WaterIntakeCalculatorForm,
)
from .models import NutritionProfile, TargetSource

_SESSION_KEY = "nutrition_onboarding"


class _OnboardingStepView(LoginRequiredMixin, View):
    """Shared guards: already-onboarded users skip straight to the
    dashboard, and a step whose prerequisite session data is missing
    (a direct link, a restarted session) bounces back to the start
    rather than crashing on a KeyError."""

    requires_keys: tuple[str, ...] = ()

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return super().dispatch(request, *args, **kwargs)
        if hasattr(request.user, "nutrition_profile"):
            return redirect("nutrition:dashboard")
        data = request.session.get(_SESSION_KEY, {})
        if any(key not in data for key in self.requires_keys):
            return redirect("nutrition:onboarding-body")
        return super().dispatch(request, *args, **kwargs)

    def _session_data(self):
        return self.request.session.get(_SESSION_KEY, {})

    def _update_session(self, **kwargs):
        data = self._session_data()
        data.update(kwargs)
        self.request.session[_SESSION_KEY] = data


class OnboardingBodyView(_OnboardingStepView):
    template_name = "nutrition/onboarding_body.html"

    def get(self, request):
        form = BodyStepForm(user=request.user)
        return render(request, self.template_name, {"form": form, "step": 1})

    def post(self, request):
        form = BodyStepForm(request.POST, user=request.user)
        if form.is_valid():
            self._update_session(
                biological_sex=form.cleaned_data["biological_sex"],
                birth_date=form.cleaned_data["birth_date"].isoformat(),
                height_m=str(form.canonical_height_m()),
                weight_kg=str(form.canonical_weight_kg()),
            )
            return redirect("nutrition:onboarding-activity")
        return render(request, self.template_name, {"form": form, "step": 1})


class OnboardingActivityView(_OnboardingStepView):
    requires_keys = ("biological_sex",)
    template_name = "nutrition/onboarding_activity.html"

    def get(self, request):
        form = ActivityInputsForm()
        return render(request, self.template_name, {"form": form, "step": 2})

    def post(self, request):
        form = ActivityInputsForm(request.POST)
        if form.is_valid():
            self._update_session(**{k: v for k, v in form.cleaned_data.items()})
            return redirect("nutrition:onboarding-activity-level")
        return render(request, self.template_name, {"form": form, "step": 2})


class OnboardingActivityLevelView(_OnboardingStepView):
    requires_keys = ("activity_job",)
    template_name = "nutrition/onboarding_activity_level.html"

    def _suggestion(self):
        data = self._session_data()
        return energy.suggest_activity_level(
            activity_job=data["activity_job"],
            daily_steps=data.get("daily_steps"),
            training_sessions_per_week=data.get("training_sessions_per_week"),
            other_exercise_minutes_per_week=data.get("other_exercise_minutes_per_week"),
        )

    def get(self, request):
        suggestion = self._suggestion()
        form = ActivityLevelConfirmForm(initial={"activity_level": suggestion.activity_level})
        return render(
            request, self.template_name, {"form": form, "suggestion": suggestion, "step": 3}
        )

    def post(self, request):
        form = ActivityLevelConfirmForm(request.POST)
        if form.is_valid():
            self._update_session(activity_level=form.cleaned_data["activity_level"])
            return redirect("nutrition:onboarding-goal")
        return render(
            request,
            self.template_name,
            {"form": form, "suggestion": self._suggestion(), "step": 3},
        )


class OnboardingGoalView(_OnboardingStepView):
    requires_keys = ("activity_level",)
    template_name = "nutrition/onboarding_goal.html"

    def get(self, request):
        form = GoalStepForm(
            user=request.user,
            initial={
                "goal_type": "maintenance",
                "target_rate": energy.DEFAULT_RATE_KG_PER_WEEK["maintenance"],
            },
        )
        return render(
            request,
            self.template_name,
            {"form": form, "step": 4, "default_rates_json": DEFAULT_RATES_JSON_SAFE},
        )

    def post(self, request):
        form = GoalStepForm(request.POST, user=request.user)
        if form.is_valid():
            target_weight_kg = form.canonical_target_weight_kg()
            self._update_session(
                goal_type=form.cleaned_data["goal_type"],
                target_weight_kg=str(target_weight_kg) if target_weight_kg is not None else None,
                target_rate=str(form.cleaned_data["target_rate"]),
            )
            return redirect("nutrition:onboarding-review")
        return render(
            request,
            self.template_name,
            {"form": form, "step": 4, "default_rates_json": DEFAULT_RATES_JSON_SAFE},
        )


def _draft_profile(data):
    """An unsaved NutritionProfile from session data — age_years and
    the other fields the energy engine needs work on an unsaved
    instance, so the review step doesn't need to persist anything to
    show its preview."""
    return NutritionProfile(
        biological_sex=data["biological_sex"],
        birth_date=date.fromisoformat(data["birth_date"]),
        activity_job=data["activity_job"],
        daily_steps=data.get("daily_steps"),
        training_sessions_per_week=data.get("training_sessions_per_week"),
        training_session_minutes=data.get("training_session_minutes"),
        other_exercise_minutes_per_week=data.get("other_exercise_minutes_per_week"),
        activity_level=data["activity_level"],
        self_reported_daily_calories=data.get("self_reported_daily_calories"),
    )


class OnboardingReviewView(_OnboardingStepView):
    requires_keys = ("goal_type",)
    template_name = "nutrition/onboarding_review.html"

    def get(self, request):
        data = self._session_data()
        profile = _draft_profile(data)
        calorie_result, macro_result = services.calculate_target_for_goal(
            profile,
            weight_kg=Decimal(data["weight_kg"]),
            height_cm=Decimal(data["height_m"]) * 100,
            goal_type=data["goal_type"],
            target_rate_kg_per_week=Decimal(data["target_rate"]),
        )
        return render(
            request,
            self.template_name,
            {"calorie_result": calorie_result, "macro_result": macro_result, "step": 5},
        )

    def post(self, request):
        data = self._session_data()
        profile = _draft_profile(data)
        weight_kg = Decimal(data["weight_kg"])
        height_cm = Decimal(data["height_m"]) * 100
        calorie_result, macro_result = services.calculate_target_for_goal(
            profile,
            weight_kg=weight_kg,
            height_cm=height_cm,
            goal_type=data["goal_type"],
            target_rate_kg_per_week=Decimal(data["target_rate"]),
        )

        user = request.user
        user.height = Decimal(data["height_m"])
        user.save(update_fields=["height"])

        profile.user = user
        profile.save()

        body_weight_type = MeasurementType.objects.filter(name="Body weight", owner=None).first()
        if body_weight_type is not None:
            BodyMeasurement.objects.create(
                user=user, measurement_type=body_weight_type, value=weight_kg
            )

        target_weight_kg = data.get("target_weight_kg")
        goal = services.set_goal(
            user,
            goal_type=data["goal_type"],
            target_rate_kg_per_week=Decimal(data["target_rate"]),
            target_weight=Decimal(target_weight_kg) if target_weight_kg else None,
        )
        services.set_target(
            user,
            goal=goal,
            daily_calories=calorie_result.daily_calories,
            macro_breakdown=macro_result,
            source=TargetSource.CALCULATED,
            reason=calorie_result.reason,
            reason_data=calorie_result.reason_data,
        )

        del request.session[_SESSION_KEY]
        return redirect("nutrition:dashboard")


def _todays_diet_plan_summary(user, today):
    """The active DietPlan's own meals for `today` specifically (all
    of them for a one-day plan, just today's weekday's for a weekly
    one — DietPlan.is_weekly's own docstring) plus their combined
    nutrition, for the dashboard's "Today's plan" card. `None` if
    there's no active plan at all, or a weekly one with nothing built
    for today's weekday (e.g. a meal slot added after the plan was
    generated for every *other* day)."""
    from .models import DietPlan

    plan = DietPlan.objects.filter(user=user, is_active=True).first()
    if plan is None:
        return None
    meals = _diet_plan_meals_with_nutrition(plan)
    if plan.is_weekly:
        meals = [meal for meal in meals if meal.weekday == today.weekday()]
    if not meals:
        return None
    total = services.ZERO_NUTRITION
    for meal in meals:
        for item in meal.items.all():
            total = total + item.nutrition
    return {"plan": plan, "meals": meals, "total": total}


class NutritionDashboardView(LoginRequiredMixin, View):
    """Redirects a not-yet-onboarded user to the wizard; otherwise
    answers spec section 16's questions at a glance: today's calories/
    macros vs. target, a weight trend chart, whether today is a
    training day (informational only — apps.nutrition.services.
    is_training_day, no separate calorie target derived from it, see
    docs/NUTRITION.md "Integration with existing apps"), and the
    dynamic-adjustment suggestion card (apps.nutrition.suggestions) —
    "is this working, does something need to change.\""""

    def get(self, request):
        if not hasattr(request.user, "nutrition_profile"):
            return redirect("nutrition:onboarding-body")
        from apps.core import units as core_units
        from apps.core.charts import build_chart_series
        from apps.measurements import units as measurement_units
        from apps.measurements.services import history_for

        from .models import NutritionGoal, NutritionTarget
        from .suggestions import suggest_calorie_adjustment

        goal = NutritionGoal.objects.filter(user=request.user, ended_at__isnull=True).first()
        target = NutritionTarget.objects.filter(user=request.user, ended_at__isnull=True).first()
        today = timezone.localdate()
        totals = services.daily_totals(request.user, today)

        weight_type = MeasurementType.objects.filter(name="Body weight", owner=None).first()
        weight_chart = None
        if weight_type is not None:
            # Charted in the user's own display unit, not canonical kg
            # — same convention as apps.measurements.views.
            # MeasurementHistoryView's own chart.
            readings = [
                (
                    measurement_units.to_display(
                        m.value, weight_type.unit_kind, request.user.unit_system
                    ),
                    m.recorded_at,
                )
                for m in history_for(request.user, weight_type, limit=30)
            ]
            weight_chart = build_chart_series(readings)
        weight_unit_label = core_units.weight_unit_label(request.user.unit_system)

        context = {
            "weight_unit_label": weight_unit_label,
            "goal": goal,
            "target": target,
            "totals": totals,
            "today": today,
            "is_training_day": services.is_training_day(request.user, today),
            "weight_chart": weight_chart,
            "suggestion": suggest_calorie_adjustment(request.user) if goal else None,
            "todays_plan": _todays_diet_plan_summary(request.user, today),
        }
        return render(request, "nutrition/dashboard.html", context)


class GoalUpdateView(LoginRequiredMixin, View):
    """Change goal_type/target_weight/target_rate after onboarding —
    reuses the exact same GoalStepForm and calculate_target_for_goal
    -> set_goal/set_target pipeline OnboardingReviewView's own POST
    uses, since a changed goal always needs a freshly derived calorie/
    macro target to match it (services.set_goal/set_target's append-
    only history keeps the old goal and target around, never
    overwritten — docs/NUTRITION.md "NutritionGoal"/"NutritionTarget").
    Not part of the onboarding wizard's session-accumulated flow
    (`_SESSION_KEY`) — the user already has a saved NutritionProfile
    by the time they can reach this, so there's nothing to accumulate
    across steps."""

    template_name = "nutrition/goal_edit.html"

    def _current_goal(self, user):
        from .models import NutritionGoal

        return NutritionGoal.objects.filter(user=user, ended_at__isnull=True).first()

    def _initial_from_goal(self, goal):
        from .models import GoalType

        if goal is None:
            return {
                "goal_type": GoalType.MAINTENANCE,
                "target_rate": energy.DEFAULT_RATE_KG_PER_WEEK["maintenance"],
            }
        initial = {"goal_type": goal.goal_type, "target_rate": goal.target_rate_kg_per_week}
        if goal.target_weight is not None:
            from apps.core import units as core_units

            initial["target_weight"] = core_units.kg_to_display(
                goal.target_weight, self.request.user.unit_system
            )
        return initial

    def get(self, request):
        goal = self._current_goal(request.user)
        form = GoalStepForm(user=request.user, initial=self._initial_from_goal(goal))
        return render(
            request,
            self.template_name,
            {"form": form, "goal": goal, "default_rates_json": DEFAULT_RATES_JSON_SAFE},
        )

    def post(self, request):
        goal = self._current_goal(request.user)
        form = GoalStepForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {"form": form, "goal": goal, "default_rates_json": DEFAULT_RATES_JSON_SAFE},
            )

        from apps.measurements.models import MeasurementType
        from apps.measurements.services import latest_for

        body_weight_type = MeasurementType.objects.filter(name="Body weight", owner=None).first()
        latest_weight = latest_for(request.user, body_weight_type) if body_weight_type else None
        if latest_weight is None:
            # Every onboarded user should have at least the body-
            # weight measurement OnboardingReviewView's own POST
            # creates — this only fires if that measurement was since
            # deleted, or the "Body weight" MeasurementType itself
            # isn't seeded on this install. The calorie engine needs a
            # current weight (Mifflin-St Jeor), so there's no
            # reasonable number to fall back to.
            form.add_error(
                None,
                _(
                    "Log a body weight measurement before changing your goal — the "
                    "calorie target needs your current weight."
                ),
            )
            return render(
                request,
                self.template_name,
                {"form": form, "goal": goal, "default_rates_json": DEFAULT_RATES_JSON_SAFE},
            )

        profile = request.user.nutrition_profile
        weight_kg = latest_weight.value
        height_cm = request.user.height * 100
        calorie_result, macro_result = services.calculate_target_for_goal(
            profile,
            weight_kg=weight_kg,
            height_cm=height_cm,
            goal_type=form.cleaned_data["goal_type"],
            target_rate_kg_per_week=form.cleaned_data["target_rate"],
        )
        new_goal = services.set_goal(
            request.user,
            goal_type=form.cleaned_data["goal_type"],
            target_rate_kg_per_week=form.cleaned_data["target_rate"],
            target_weight=form.canonical_target_weight_kg(),
        )
        services.set_target(
            request.user,
            goal=new_goal,
            daily_calories=calorie_result.daily_calories,
            macro_breakdown=macro_result,
            source=TargetSource.CALCULATED,
            reason=calorie_result.reason,
            reason_data=calorie_result.reason_data,
        )
        messages.success(
            request,
            _("Goal updated — new target is %(calories)d kcal/day.")
            % {"calories": calorie_result.daily_calories},
        )
        return redirect("nutrition:dashboard")


class FoodListView(LoginRequiredMixin, ListView):
    template_name = "nutrition/food_list.html"
    context_object_name = "foods"
    paginate_by = 50

    def get_queryset(self):
        from django.db.models import Q

        from .models import Food

        qs = Food.objects.filter(
            Q(owner=self.request.user) | Q(owner__isnull=True), active=True
        ).order_by("name")
        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(name__icontains=query)
        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "")
        return context


class FoodCreateView(LoginRequiredMixin, CreateView):
    form_class = FoodForm
    template_name = "nutrition/food_form.html"

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)

    def get_success_url(self):
        from django.urls import reverse

        return reverse("nutrition:food-list")


class FoodSearchResultsView(LoginRequiredMixin, View):
    """HTMX partial — local + live OpenFoodFacts results for every
    "search for a food and add it" box in the app
    (apps.nutrition.services.search_foods): the food diary, adding a
    recipe ingredient, and adding an extra item to a diet plan meal.
    One search implementation, one results partial — `mode` (plus
    whatever extra id that mode needs) only changes which hidden
    fields/POST target the results partial renders, not how the
    search itself works."""

    template_name = "nutrition/_food_search_results.html"

    def get(self, request):
        query = request.GET.get("q", "").strip()
        mode = request.GET.get("mode", "diary")
        local, off_results = ([], []) if not query else services.search_foods(request.user, query)
        context = {
            "local": local,
            "off_results": off_results,
            "query": query,
            "mode": mode,
        }
        if mode == "diary":
            context["date"] = request.GET.get("date", "")
            context["meal_slot_id"] = request.GET.get("meal_slot", "")
        elif mode == "recipe":
            context["recipe_pk"] = request.GET.get("recipe_pk", "")
        elif mode == "diet-plan-meal":
            context["plan_pk"] = request.GET.get("plan_pk", "")
            context["meal_pk"] = request.GET.get("meal_pk", "")
        # mode == "browse" needs no extra id — importing to the shared
        # library isn't scoped to a diary date, a recipe, or a meal.
        return render(request, self.template_name, context)


class FoodBrowseView(LoginRequiredMixin, View):
    """Search-or-browse-by-category, import straight into the shared
    food library — distinct from FoodSearchResultsView's other three
    modes in that there's nothing to log a quantity *into* here, just
    "add this to what everyone on this instance can find." See
    docs/NUTRITION.md "OpenFoodFacts integration"."""

    def get(self, request):
        return render(
            request,
            "nutrition/food_browse.html",
            {"categories": services.suggested_categories()},
        )


class FoodCategoryView(LoginRequiredMixin, View):
    template_name = "nutrition/food_category.html"

    def get(self, request, category_id):
        return render(
            request,
            self.template_name,
            {
                "category_id": category_id,
                "off_results": services.browse_category(category_id),
            },
        )


@login_required
def food_import(request):
    """Imports one OpenFoodFacts product straight into the shared
    library (owner=None, same as every other OFF import) — the POST
    target for both the browse search box and a category listing's
    "Import" buttons. No quantity: this adds the food to the library,
    it doesn't log anything."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    barcode = request.POST.get("off_barcode", "").strip()
    if barcode:
        services.import_or_refresh_food_from_off(barcode)
    next_url = request.POST.get("next", "")
    if next_url and url_has_allowed_host_and_scheme(
        next_url, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(next_url)
    return redirect("nutrition:food-list")


def _parse_diary_date(value):
    if not value:
        return timezone.localdate()
    try:
        return date.fromisoformat(value)
    except ValueError:
        return timezone.localdate()


class DiaryDayView(LoginRequiredMixin, View):
    """The food diary for one day, grouped by meal slot — spec section
    8: add/edit/remove a food, see calories and macros, see how much
    is left against today's target."""

    template_name = "nutrition/diary_day.html"

    def get(self, request, target_date=None):
        from .models import DiaryEntry, NutritionTarget

        target_date = _parse_diary_date(target_date)
        # A plain list, not a queryset, from here on: each slot gets
        # its own `.entries` attribute below so the template can do a
        # simple `{% for entry in slot.entries %}` — Django templates
        # have no clean way to look a dict up by a *variable* key
        # (`entries_by_slot[slot.pk]`) without a custom filter, so the
        # entries are attached directly to the objects that already
        # carry the right key instead.
        meal_slots = list(services.visible_meal_slots(request.user))
        entries = (
            DiaryEntry.objects.filter(user=request.user, date=target_date)
            .select_related("food", "recipe", "meal_slot")
            .order_by("meal_slot__order", "created_at")
        )
        entries_by_slot_id = {slot.pk: [] for slot in meal_slots}
        for entry in entries:
            entry.nutrition = services.diary_entry_nutrition(entry)
            entries_by_slot_id.setdefault(entry.meal_slot_id, []).append(entry)
        for slot in meal_slots:
            slot.entries = entries_by_slot_id.get(slot.pk, [])

        totals = services.daily_totals(request.user, target_date)
        target = NutritionTarget.objects.filter(
            user=request.user, ended_at__isnull=True
        ).first()

        return render(
            request,
            self.template_name,
            {
                "date": target_date,
                "previous_date": target_date - timezone.timedelta(days=1),
                "next_date": target_date + timezone.timedelta(days=1),
                "meal_slots": meal_slots,
                "totals": totals,
                "target": target,
            },
        )


@login_required
def diary_day_copy(request, source_date):
    """Duplicates one day's whole diary onto another date
    (apps.nutrition.services.copy_diary_day) — "I ate the same as
    yesterday" without re-adding every item by hand. POST-only, same
    convention as every other diary mutation here."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    source_date = _parse_diary_date(source_date)
    target_date = _parse_diary_date(request.POST.get("target_date"))
    if not request.POST.get("target_date"):
        messages.error(request, _("Pick a date to copy this day to."))
        return redirect("nutrition:diary-day", target_date=source_date.isoformat())
    count = services.copy_diary_day(request.user, source_date, target_date)
    if count:
        message = ngettext(
            "Copied %(count)d entry to %(date)s.",
            "Copied %(count)d entries to %(date)s.",
            count,
        ) % {"count": count, "date": target_date.isoformat()}
        messages.success(request, message)
    else:
        messages.info(request, _("Nothing to copy — that day has no entries."))
    return redirect("nutrition:diary-day", target_date=target_date.isoformat())


class DiaryAddEntryView(LoginRequiredMixin, View):
    template_name = "nutrition/diary_add_entry.html"

    def get(self, request):
        target_date = request.GET.get("date", "")
        return render(
            request,
            self.template_name,
            {
                "date": target_date or timezone.localdate().isoformat(),
                "meal_slots": services.visible_meal_slots(request.user),
                "most_used": services.most_used_foods(request.user),
            },
        )

    def post(self, request):
        from .models import DiaryEntry, Food

        form = DiaryAddEntryForm(request.POST, user=request.user)
        target_date = _parse_diary_date(request.POST.get("date"))
        if not form.is_valid():
            return render(
                request,
                self.template_name,
                {
                    "form": form,
                    "date": target_date.isoformat(),
                    "meal_slots": services.visible_meal_slots(request.user),
                    "most_used": services.most_used_foods(request.user),
                },
            )

        food = None
        if form.cleaned_data.get("food_id"):
            food = get_object_or_404(
                Food.objects.filter(active=True), pk=form.cleaned_data["food_id"]
            )
        elif form.cleaned_data.get("off_barcode"):
            food = services.import_or_refresh_food_from_off(form.cleaned_data["off_barcode"])
            if food is None:
                form.add_error(None, _("That food couldn't be imported — try again."))
                return render(
                    request,
                    self.template_name,
                    {
                        "form": form,
                        "date": target_date.isoformat(),
                        "meal_slots": services.visible_meal_slots(request.user),
                    },
                )

        DiaryEntry.objects.create(
            user=request.user,
            date=target_date,
            meal_slot=form.cleaned_data["meal_slot"],
            food=food,
            quantity=form.cleaned_data["quantity"],
        )
        return redirect("nutrition:diary-day", target_date=target_date.isoformat())


def _owned_diary_entry_or_404(request, pk):
    from .models import DiaryEntry

    return get_object_or_404(DiaryEntry, pk=pk, user=request.user)


@login_required
def diary_entry_edit(request, pk):
    entry = _owned_diary_entry_or_404(request, pk)
    if request.method == "POST":
        form = DiaryEntryQuantityForm(request.POST, instance=entry)
        if form.is_valid():
            form.save()
            return redirect("nutrition:diary-day", target_date=entry.date.isoformat())
    else:
        form = DiaryEntryQuantityForm(instance=entry)
    return render(request, "nutrition/diary_entry_form.html", {"form": form, "entry": entry})


@login_required
def diary_entry_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    entry = _owned_diary_entry_or_404(request, pk)
    target_date = entry.date
    entry.delete()
    return redirect("nutrition:diary-day", target_date=target_date.isoformat())


class RecipeListView(LoginRequiredMixin, ListView):
    template_name = "nutrition/recipe_list.html"
    context_object_name = "recipes"

    def get_queryset(self):
        from django.db.models import Q

        from .models import Recipe

        # Same owner-or-shared visibility as everywhere else a shared
        # Food shows up (e.g. DietPlanItemForm) — a built-in template
        # recipe (owner=None) is listed for every user, not just its
        # own list of ones they wrote themselves.
        qs = Recipe.objects.filter(
            Q(owner=self.request.user) | Q(owner__isnull=True)
        ).select_related("meal_slot").order_by("name")
        query = self.request.GET.get("q", "").strip()
        if query:
            qs = qs.filter(name__icontains=query)
        return qs

    def get_context_data(self, **kwargs):
        from decimal import Decimal

        from .models import RecipeIngredient

        context = super().get_context_data(**kwargs)
        context["query"] = self.request.GET.get("q", "")
        # Per-serving calories at a glance, without opening each
        # recipe — the same reason food_list.html shows calories
        # directly rather than making every recipe a guess until
        # opened. Computed as one bulk query across every listed
        # recipe's ingredients (not services.recipe_per_serving_
        # nutrition called once per recipe in a loop, which would be a
        # real N+1 here — this is a *list* of recipes, unlike that
        # function's other call sites which each only ever look at one
        # recipe at a time).
        recipes = list(context["recipes"])
        totals_by_recipe = {}
        ingredients = RecipeIngredient.objects.filter(
            recipe_id__in=[recipe.pk for recipe in recipes]
        ).select_related("food")
        for ingredient in ingredients:
            totals_by_recipe[ingredient.recipe_id] = totals_by_recipe.get(
                ingredient.recipe_id, services.ZERO_NUTRITION
            ) + services.scale_nutrition(ingredient.food, ingredient.quantity)
        for recipe in recipes:
            total = totals_by_recipe.get(recipe.pk, services.ZERO_NUTRITION)
            servings = Decimal(recipe.servings) if recipe.servings else Decimal("1")
            recipe.per_serving = total.scaled_by(Decimal("1") / servings)
        context["recipes"] = recipes
        return context


class RecipeDetailView(LoginRequiredMixin, View):
    template_name = "nutrition/recipe_detail.html"

    def get(self, request, pk):
        recipe = _viewable_recipe_or_404(request, pk)
        ingredients = list(recipe.ingredients.select_related("food"))
        for ingredient in ingredients:
            ingredient.nutrition = services.scale_nutrition(ingredient.food, ingredient.quantity)
        return render(
            request,
            self.template_name,
            {
                "recipe": recipe,
                "ingredients": ingredients,
                "total": services.recipe_total_nutrition(recipe),
                "per_serving": services.recipe_per_serving_nutrition(recipe),
                "log_form": LogRecipeForm(user=request.user),
            },
        )


def _owned_recipe_or_404(request, pk):
    """For any *mutating* action (edit/delete the recipe itself, or
    add/edit/remove an ingredient) — a built-in template recipe
    (owner=None) never matches `owner=request.user`, so this 404s for
    it exactly as it would for another user's own recipe. That's the
    whole access control for "shared but read-only": no separate
    permission check needed, just never routing a write through the
    read-only helper below."""
    from .models import Recipe

    return get_object_or_404(Recipe, pk=pk, owner=request.user)


def _viewable_recipe_or_404(request, pk):
    """For read-only access (the detail page, logging it to your own
    diary) — a user's own recipe, or a shared, built-in template
    recipe (owner=None), same visibility as RecipeListView.get_queryset
    above."""
    from django.db.models import Q

    from .models import Recipe

    return get_object_or_404(Recipe, Q(owner=request.user) | Q(owner__isnull=True), pk=pk)


@login_required
def recipe_create(request):
    form = RecipeForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        recipe = form.save(commit=False)
        recipe.owner = request.user
        recipe.save()
        messages.success(
            request,
            _('"%(name)s" created — now add its ingredients below.') % {"name": recipe.name},
        )
        return redirect("nutrition:recipe-detail", pk=recipe.pk)
    return render(request, "nutrition/recipe_form.html", {"form": form})


@login_required
def recipe_update(request, pk):
    recipe = _owned_recipe_or_404(request, pk)
    form = RecipeForm(request.POST or None, instance=recipe, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("nutrition:recipe-detail", pk=recipe.pk)
    return render(request, "nutrition/recipe_form.html", {"form": form, "recipe": recipe})


@login_required
def recipe_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    recipe = _owned_recipe_or_404(request, pk)
    recipe.delete()
    return redirect("nutrition:recipe-list")


@login_required
def recipe_ingredient_create(request, recipe_pk):
    """Search-and-pick, same shape as DiaryAddEntryView below — a
    recipe's macros come entirely from its ingredients' own Food
    rows, so finding/importing the right one has to be at least as
    easy here as it already is in the food diary, not a bare dropdown
    of foods the user already had to create by hand elsewhere."""
    from .models import Food, RecipeIngredient

    recipe = _owned_recipe_or_404(request, recipe_pk)
    if request.method != "POST":
        return render(
            request,
            "nutrition/recipe_ingredient_form.html",
            {"recipe": recipe, "most_used": services.most_used_foods(request.user)},
        )

    form = RecipeIngredientSearchForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "nutrition/recipe_ingredient_form.html",
            {"form": form, "recipe": recipe},
        )

    food = None
    if form.cleaned_data.get("food_id"):
        food = get_object_or_404(
            Food.objects.filter(active=True), pk=form.cleaned_data["food_id"]
        )
    elif form.cleaned_data.get("off_barcode"):
        food = services.import_or_refresh_food_from_off(form.cleaned_data["off_barcode"])
        if food is None:
            form.add_error(None, _("That food couldn't be imported — try again."))
            return render(
                request,
                "nutrition/recipe_ingredient_form.html",
                {"form": form, "recipe": recipe},
            )

    next_order = (
        recipe.ingredients.aggregate(highest=Max("order"))["highest"] or -1
    ) + 1
    RecipeIngredient.objects.create(
        recipe=recipe, food=food, quantity=form.cleaned_data["quantity"], order=next_order
    )
    return redirect("nutrition:recipe-detail", pk=recipe.pk)


@login_required
def recipe_ingredient_edit(request, recipe_pk, pk):
    """Changes only the quantity of an ingredient already on the
    recipe — see RecipeIngredientQuantityForm's own docstring for why
    this exists alongside create/delete rather than requiring a
    delete-and-re-add round trip for a simple amount correction."""
    from .models import RecipeIngredient

    recipe = _owned_recipe_or_404(request, recipe_pk)
    ingredient = get_object_or_404(RecipeIngredient, pk=pk, recipe=recipe)
    if request.method == "POST":
        form = RecipeIngredientQuantityForm(request.POST, instance=ingredient)
        if form.is_valid():
            form.save()
            return redirect("nutrition:recipe-detail", pk=recipe.pk)
    else:
        form = RecipeIngredientQuantityForm(instance=ingredient)
    return render(
        request,
        "nutrition/recipe_ingredient_edit_form.html",
        {"form": form, "recipe": recipe, "ingredient": ingredient},
    )


@login_required
def recipe_ingredient_delete(request, recipe_pk, pk):
    from .models import RecipeIngredient

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    recipe = _owned_recipe_or_404(request, recipe_pk)
    ingredient = get_object_or_404(RecipeIngredient, pk=pk, recipe=recipe)
    ingredient.delete()
    return redirect("nutrition:recipe-detail", pk=recipe.pk)


@login_required
def recipe_log(request, pk):
    from .models import DiaryEntry

    # Viewable, not owned-only — logging a shared template recipe to
    # your own diary is exactly what it's there for.
    recipe = _viewable_recipe_or_404(request, pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = LogRecipeForm(request.POST, user=request.user)
    if not form.is_valid():
        ingredients = list(recipe.ingredients.select_related("food"))
        return render(
            request,
            "nutrition/recipe_detail.html",
            {
                "recipe": recipe,
                "ingredients": ingredients,
                "total": services.recipe_total_nutrition(recipe),
                "per_serving": services.recipe_per_serving_nutrition(recipe),
                "log_form": form,
            },
        )
    entry = DiaryEntry.objects.create(
        user=request.user,
        date=form.cleaned_data["date"],
        meal_slot=form.cleaned_data["meal_slot"],
        recipe=recipe,
        quantity=form.cleaned_data["quantity"],
    )
    return redirect("nutrition:diary-day", target_date=entry.date.isoformat())


class DietPlanListView(LoginRequiredMixin, ListView):
    template_name = "nutrition/diet_plan_list.html"
    context_object_name = "plans"

    def get_queryset(self):
        from .models import DietPlan

        return DietPlan.objects.filter(user=self.request.user).order_by("-created_at")


class DietPlanCreateView(LoginRequiredMixin, View):
    """Step 1 (and only step — see docs/NUTRITION.md "Diet builder
    wizard") of the diet builder: one form, pre-filled from the active
    target, generates the whole plan on submit. Review/swap happens on
    the plan's own detail page afterward, not as further wizard steps —
    unlike onboarding, there's nothing here that depends on an earlier
    answer to render the next question."""

    template_name = "nutrition/diet_plan_form.html"

    def get(self, request):
        from .models import NutritionTarget

        target = NutritionTarget.objects.filter(
            user=request.user, ended_at__isnull=True
        ).first()
        # Pre-checked, not left for the user to tick one by one — every
        # other field on this step is already pre-filled from real data
        # (the active target, or "My diet plan"); leaving a required
        # multi-select empty by default is the one inconsistent gap,
        # and "build a plan covering all my meals" is what most people
        # opening this page actually want. Still fully editable.
        initial = {
            "name": "My diet plan",
            "meal_slots": [slot.pk for slot in services.visible_meal_slots(request.user)],
        }
        if target is not None:
            initial.update(
                target_calories=target.daily_calories,
                target_protein_grams=target.protein_grams,
                target_carbohydrate_grams=target.carbohydrate_grams,
                target_fat_grams=target.fat_grams,
            )
        form = DietPlanForm(user=request.user, initial=initial)
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        from .models import NutritionTarget

        form = DietPlanForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form})

        target = NutritionTarget.objects.filter(
            user=request.user, ended_at__isnull=True
        ).first()
        plan = diet_builder.build_diet_plan(
            request.user,
            name=form.cleaned_data["name"],
            goal=target.goal if target else None,
            target_calories=form.cleaned_data["target_calories"],
            target_protein_grams=form.cleaned_data["target_protein_grams"],
            target_carbohydrate_grams=form.cleaned_data["target_carbohydrate_grams"],
            target_fat_grams=form.cleaned_data["target_fat_grams"],
            meal_slots=list(form.cleaned_data["meal_slots"]),
            is_weekly=form.cleaned_data["is_weekly"],
        )
        return redirect("nutrition:diet-plan-detail", pk=plan.pk)


def _owned_diet_plan_or_404(request, pk):
    from .models import DietPlan

    return get_object_or_404(DietPlan, pk=pk, user=request.user)


def _diet_plan_meals_with_nutrition(plan):
    """The plan's meals/items, each item annotated with its computed
    `.nutrition` — shared by the detail page and diet_plan_log's own
    error re-render below, so there's exactly one place this query
    shape and the food-vs-recipe branch live."""
    from .models import DietPlanItem

    meals = list(
        plan.meals.select_related("meal_slot").prefetch_related(
            Prefetch(
                "items",
                queryset=DietPlanItem.objects.select_related("food", "recipe"),
            )
        )
    )
    for meal in meals:
        # Only meaningful for a weekly plan (DietPlan.is_weekly) — None
        # for a one-day plan's meals, same as their own weekday field.
        # apps.programs.models.Weekday is the same 0=Monday..6=Sunday
        # numbering DietPlanMeal.weekday itself uses (Python's own
        # date.weekday()) — reused rather than a second copy of the
        # same seven translated day names.
        meal.weekday_name = (
            ProgramsWeekday(meal.weekday).label if meal.weekday is not None else None
        )
        for item in meal.items.all():
            item.nutrition = (
                services.scale_nutrition(item.food, item.quantity)
                if item.food_id
                else services.recipe_per_serving_nutrition(item.recipe).scaled_by(
                    item.quantity
                )
            )
        # "So far" against the meal's own target — meaningful now that
        # a meal can hold more than the one item diet_builder
        # originally generated for it (diet_plan_meal_item_add below).
        meal.actual_calories = sum(
            (item.nutrition.calories for item in meal.items.all()), Decimal("0")
        )
    return meals


class DietPlanDetailView(LoginRequiredMixin, View):
    template_name = "nutrition/diet_plan_detail.html"

    def get(self, request, pk):
        plan = _owned_diet_plan_or_404(request, pk)
        return render(
            request,
            self.template_name,
            {
                "plan": plan,
                "meals": _diet_plan_meals_with_nutrition(plan),
                "log_form": LogDietPlanForm(initial={"date": timezone.localdate()}),
            },
        )


@login_required
def diet_plan_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    plan = _owned_diet_plan_or_404(request, pk)
    plan.delete()
    return redirect("nutrition:diet-plan-list")


@login_required
def diet_plan_toggle_active(request, pk):
    """One button, both directions: activates this plan (and
    deactivates whichever other one was active — only one ever is,
    services.set_active_diet_plan's own docstring) if it wasn't
    already, or turns it off entirely if it was."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    plan = _owned_diet_plan_or_404(request, pk)
    if plan.is_active:
        services.deactivate_diet_plan(plan)
    else:
        services.set_active_diet_plan(request.user, plan)
    next_url = request.POST.get("next")
    if next_url:
        return redirect(next_url)
    return redirect("nutrition:diet-plan-detail", pk=plan.pk)


@login_required
def diet_plan_item_edit(request, plan_pk, pk):
    from .models import DietPlanItem

    plan = _owned_diet_plan_or_404(request, plan_pk)
    item = get_object_or_404(DietPlanItem, pk=pk, diet_plan_meal__diet_plan=plan)
    form = DietPlanItemForm(request.POST or None, instance=item, user=request.user)
    if request.method == "POST" and form.is_valid():
        form.save()
        return redirect("nutrition:diet-plan-detail", pk=plan.pk)
    return render(
        request, "nutrition/diet_plan_item_form.html", {"form": form, "plan": plan, "item": item}
    )


@login_required
def diet_plan_item_delete(request, plan_pk, pk):
    from .models import DietPlanItem

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    plan = _owned_diet_plan_or_404(request, plan_pk)
    item = get_object_or_404(DietPlanItem, pk=pk, diet_plan_meal__diet_plan=plan)
    item.delete()
    return redirect("nutrition:diet-plan-detail", pk=plan.pk)


@login_required
def diet_plan_meal_item_add(request, plan_pk, meal_pk):
    """Search-and-pick, same shape as recipe_ingredient_create above —
    meal planning shouldn't be locked to whatever single item
    diet_builder originally generated for a meal (see that module's
    own "deliberately simple, not a knapsack solver" scope note); a
    user filling out a real day's plan needs to be able to add more to
    a meal, not just swap its one item."""
    from .models import DietPlanMeal, Food

    plan = _owned_diet_plan_or_404(request, plan_pk)
    meal = get_object_or_404(DietPlanMeal, pk=meal_pk, diet_plan=plan)
    if request.method != "POST":
        return render(
            request,
            "nutrition/diet_plan_meal_item_form.html",
            {"plan": plan, "meal": meal, "most_used": services.most_used_foods(request.user)},
        )

    form = DietPlanMealItemSearchForm(request.POST)
    if not form.is_valid():
        return render(
            request,
            "nutrition/diet_plan_meal_item_form.html",
            {"form": form, "plan": plan, "meal": meal},
        )

    food = None
    if form.cleaned_data.get("food_id"):
        food = get_object_or_404(
            Food.objects.filter(active=True), pk=form.cleaned_data["food_id"]
        )
    elif form.cleaned_data.get("off_barcode"):
        food = services.import_or_refresh_food_from_off(form.cleaned_data["off_barcode"])
        if food is None:
            form.add_error(None, _("That food couldn't be imported — try again."))
            return render(
                request,
                "nutrition/diet_plan_meal_item_form.html",
                {"form": form, "plan": plan, "meal": meal},
            )

    next_order = (meal.items.aggregate(highest=Max("order"))["highest"] or -1) + 1
    meal.items.create(food=food, quantity=form.cleaned_data["quantity"], order=next_order)
    return redirect("nutrition:diet-plan-detail", pk=plan.pk)


@login_required
def diet_plan_log(request, pk):
    plan = _owned_diet_plan_or_404(request, pk)
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    form = LogDietPlanForm(request.POST)
    if not form.is_valid():
        # Same pattern as recipe_log below: re-render with the errors
        # visible rather than silently bouncing back to a page that
        # looks identical to before the submit, which left the user
        # with no idea anything went wrong.
        return render(
            request,
            "nutrition/diet_plan_detail.html",
            {"plan": plan, "meals": _diet_plan_meals_with_nutrition(plan), "log_form": form},
        )
    target_date = form.cleaned_data["date"]
    created = diet_builder.apply_diet_plan(plan, target_date)
    if created:
        message = ngettext(
            "Logged %(count)d item to %(date)s.",
            "Logged %(count)d items to %(date)s.",
            len(created),
        ) % {"count": len(created), "date": target_date.isoformat()}
        messages.success(request, message)
    else:
        # Realistic for a weekly plan (DietPlan.is_weekly) whose
        # target date's weekday has nothing built for it — e.g. a meal
        # slot added after the plan was generated for every other day.
        # A one-day plan always has *some* meal, so this was
        # unreachable before weekly plans existed.
        messages.info(request, _("Nothing to log — this plan has no items for that date."))
    return redirect("nutrition:diary-day", target_date=target_date.isoformat())


@login_required
def accept_adjustment_suggestion(request):
    """Accepting the dashboard's dynamic-adjustment card — never
    automatic (docs/NUTRITION.md "Dynamic calorie adjustment"), always
    this one explicit POST from a user action."""
    from .suggestions import AdjustmentAction, suggest_calorie_adjustment

    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    suggestion = suggest_calorie_adjustment(request.user)
    if suggestion.action == AdjustmentAction.ADJUST:
        services.apply_adjustment_suggestion(request.user, suggestion)
    return redirect("nutrition:dashboard")


class CalculatorsHomeView(LoginRequiredMixin, View):
    """A small hub linking to the standalone calculators below — see
    docs/NUTRITION.md "Calculators". None of these need a nutrition
    profile or an active goal, unlike the rest of this app, so this
    page (unlike NutritionDashboardView) never redirects into the
    onboarding wizard."""

    def get(self, request):
        return render(request, "nutrition/calculators_home.html")


class _CalculatorView(LoginRequiredMixin, View):
    """Shared shape for every calculator below: a GET-only form (no
    side effect, so a plain GET with query-string input is the right
    verb — bookmarkable/shareable, same convention as
    FoodSearchResultsView), computing and showing a result inline on
    the same page when the query string carries a complete, valid
    submission."""

    template_name: str
    form_class: type

    def get(self, request):
        submitted = bool(request.GET)
        form = self.form_class(request.GET or None, user=request.user)
        result = self.compute(form) if submitted and form.is_valid() else None
        return render(
            request, self.template_name, {"form": form, "result": result, "submitted": submitted}
        )

    def compute(self, form):
        raise NotImplementedError


class BmrTdeeCalculatorView(_CalculatorView):
    template_name = "nutrition/calculator_bmr_tdee.html"
    form_class = BmrTdeeCalculatorForm

    def compute(self, form):
        bmr = energy.calculate_bmr(
            weight_kg=form.canonical_weight_kg(),
            height_cm=form.canonical_height_cm(),
            age_years=form.cleaned_data["age_years"],
            biological_sex=form.cleaned_data["biological_sex"],
        )
        tdee = energy.calculate_tdee(bmr, form.cleaned_data["activity_level"])
        return {"bmr": bmr, "tdee": tdee}


class MacroCalculatorView(_CalculatorView):
    template_name = "nutrition/calculator_macros.html"
    form_class = MacroCalculatorForm

    def compute(self, form):
        from . import macros as macros_module

        return macros_module.calculate_macros(
            weight_kg=form.canonical_weight_kg(),
            daily_calories=form.cleaned_data["daily_calories"],
            goal_type=form.cleaned_data["goal_type"],
        )


class BodyFatCalculatorView(_CalculatorView):
    template_name = "nutrition/calculator_body_fat.html"
    form_class = BodyFatCalculatorForm

    def compute(self, form):
        from . import calculators

        return calculators.estimate_body_fat_percent(
            biological_sex=form.cleaned_data["biological_sex"],
            height_cm=form.canonical_height_cm(),
            neck_cm=form.canonical_neck_cm(),
            waist_cm=form.canonical_waist_cm(),
            hip_cm=form.canonical_hip_cm(),
        )


class WaterIntakeCalculatorView(_CalculatorView):
    template_name = "nutrition/calculator_water_intake.html"
    form_class = WaterIntakeCalculatorForm

    def compute(self, form):
        from . import calculators

        return calculators.estimate_daily_water_liters(
            weight_kg=form.canonical_weight_kg(),
            activity_level=form.cleaned_data["activity_level"],
        )


class BMICalculatorView(_CalculatorView):
    """Thin wrapper around apps.core.bmi — not a second BMI
    implementation, the same "quick number without setting anything
    up" framing as every other calculator here, for the same
    calculation the Body weight measurement history page already
    shows once a height is on file."""

    template_name = "nutrition/calculator_bmi.html"
    form_class = BMICalculatorForm

    def compute(self, form):
        from apps.core import bmi as core_bmi
        from apps.core import units as core_units

        height_m = core_units.cm_to_meters(form.canonical_height_cm())
        weight_kg = form.canonical_weight_kg()
        value = core_bmi.calculate_bmi(weight_kg, height_m)
        return {
            "bmi": value,
            "category": core_bmi.category_for(value) if value is not None else None,
            "rows": core_bmi.category_rows(height_m, self.request.user.unit_system),
        }


class WaistHipRatioCalculatorView(_CalculatorView):
    template_name = "nutrition/calculator_waist_hip_ratio.html"
    form_class = WaistHipRatioCalculatorForm

    def compute(self, form):
        from . import calculators

        ratio = calculators.calculate_waist_hip_ratio(
            waist_cm=form.canonical_waist_cm(), hip_cm=form.canonical_hip_cm()
        )
        risk = None
        if ratio is not None:
            risk = calculators.whr_risk_level(ratio, form.cleaned_data["biological_sex"])
        return {"ratio": ratio, "risk": risk}


class TimeToGoalCalculatorView(_CalculatorView):
    template_name = "nutrition/calculator_time_to_goal.html"
    form_class = TimeToGoalCalculatorForm

    def compute(self, form):
        from . import calculators

        weeks = calculators.estimate_weeks_to_goal(
            current_weight_kg=form.canonical_current_weight_kg(),
            target_weight_kg=form.canonical_target_weight_kg(),
            rate_kg_per_week=form.canonical_rate_kg_per_week(),
        )
        target_date = None
        if weeks is not None:
            target_date = timezone.localdate() + timezone.timedelta(days=round(float(weeks) * 7))
        return {"weeks": weeks, "target_date": target_date}


class NutritionStatsView(LoginRequiredMixin, View):
    """"How much have I actually been eating lately" — the calorie
    trend the daily diary total can't show on its own, since it only
    ever shows one day at a time. A single fixed 30-day window, not a
    range picker like apps.analytics's own stats page: a month is
    already the natural "am I actually consistent" window for calorie
    tracking, and this page has one chart, not several — the extra
    control apps.analytics needs to keep several charts legible isn't
    earning its keep here yet (docs/NUTRITION.md "Nutrition
    statistics")."""

    template_name = "nutrition/stats.html"

    def get(self, request):
        from apps.core.charts import build_bar_series

        from .models import NutritionTarget

        history = services.calorie_history(request.user)
        summary = services.nutrition_stats(request.user)
        target = NutritionTarget.objects.filter(
            user=request.user, ended_at__isnull=True
        ).first()
        calorie_chart = build_bar_series(
            [
                (day.strftime("%b %d"), totals.calories.quantize(Decimal("1")))
                for day, totals in history
            ]
        )
        return render(
            request,
            self.template_name,
            {
                "calorie_chart": calorie_chart,
                "summary": summary,
                "target": target,
            },
        )
