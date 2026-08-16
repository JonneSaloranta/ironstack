"""The nutrition onboarding wizard — see docs/NUTRITION.md "Phased
implementation plan" step 3. Five small steps (spec: step-by-step, not
one giant form), state accumulated in the session between them
(`request.session["nutrition_onboarding"]`, Decimal/date values kept
as plain strings since the session is JSON-serialized) and committed
atomically only on the last step's POST.
"""

from datetime import date
from decimal import Decimal

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.generic import CreateView, ListView, View

from apps.measurements.models import BodyMeasurement, MeasurementType

from . import energy, services
from .forms import (
    DEFAULT_RATES_JSON_SAFE,
    ActivityInputsForm,
    ActivityLevelConfirmForm,
    BodyStepForm,
    DiaryAddEntryForm,
    DiaryEntryQuantityForm,
    FoodForm,
    GoalStepForm,
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
        )

        del request.session[_SESSION_KEY]
        return redirect("nutrition:dashboard")


class NutritionDashboardView(LoginRequiredMixin, View):
    """Redirects a not-yet-onboarded user to the wizard; otherwise
    today's calories/macros vs. target (spec section 16 — "how much
    should I eat today, how much have I eaten"). Training-day
    awareness and the dynamic-adjustment suggestion card land here in
    a later phase (docs/NUTRITION.md phases 6/9), once the food diary
    below has real data behind it."""

    def get(self, request):
        if not hasattr(request.user, "nutrition_profile"):
            return redirect("nutrition:onboarding-body")
        from .models import NutritionGoal, NutritionTarget

        goal = NutritionGoal.objects.filter(user=request.user, ended_at__isnull=True).first()
        target = NutritionTarget.objects.filter(user=request.user, ended_at__isnull=True).first()
        today = timezone.localdate()
        totals = services.daily_totals(request.user, today)
        return render(
            request,
            "nutrition/dashboard.html",
            {"goal": goal, "target": target, "totals": totals, "today": today},
        )


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
    """HTMX partial — local + live OpenFoodFacts results for the
    add-to-diary search box (apps.nutrition.services.search_foods)."""

    template_name = "nutrition/_food_search_results.html"

    def get(self, request):
        query = request.GET.get("q", "").strip()
        target_date = request.GET.get("date", "")
        meal_slot_id = request.GET.get("meal_slot", "")
        local, off_results = ([], []) if not query else services.search_foods(request.user, query)
        return render(
            request,
            self.template_name,
            {
                "local": local,
                "off_results": off_results,
                "date": target_date,
                "meal_slot_id": meal_slot_id,
                "query": query,
            },
        )


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
                form.add_error(None, "That food couldn't be imported — try again.")
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


def diary_entry_delete(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    entry = _owned_diary_entry_or_404(request, pk)
    target_date = entry.date
    entry.delete()
    return redirect("nutrition:diary-day", target_date=target_date.isoformat())
