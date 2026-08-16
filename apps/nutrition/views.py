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
from django.shortcuts import redirect, render
from django.views.generic import View

from apps.measurements.models import BodyMeasurement, MeasurementType

from . import energy, services
from .forms import (
    DEFAULT_RATES_JSON_SAFE,
    ActivityInputsForm,
    ActivityLevelConfirmForm,
    BodyStepForm,
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
    """Placeholder — filled in properly once the food diary/dashboard
    phase lands (docs/NUTRITION.md phase 7). For now: redirects a
    not-yet-onboarded user to the wizard, otherwise a minimal summary."""

    def get(self, request):
        if not hasattr(request.user, "nutrition_profile"):
            return redirect("nutrition:onboarding-body")
        from .models import NutritionGoal, NutritionTarget

        goal = NutritionGoal.objects.filter(user=request.user, ended_at__isnull=True).first()
        target = NutritionTarget.objects.filter(user=request.user, ended_at__isnull=True).first()
        return render(
            request, "nutrition/dashboard.html", {"goal": goal, "target": target}
        )
