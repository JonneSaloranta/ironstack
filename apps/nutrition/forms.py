"""Forms for the nutrition onboarding wizard — see docs/NUTRITION.md
"Phased implementation plan" step 3. Each step is a small, focused
form (spec: "a step-by-step onboarding," not one giant form); the
wizard view (apps.nutrition.views) accumulates answers in the session
between steps and commits everything atomically on the last one.
"""

from decimal import Decimal

from django import forms
from django.utils.translation import gettext_lazy as _

from apps.core import units as core_units

from . import energy
from .models import ActivityJob, ActivityLevel, BiologicalSex, Food, GoalType, Recipe


class BodyStepForm(forms.Form):
    biological_sex = forms.ChoiceField(
        choices=BiologicalSex.choices,
        label=_("Biological sex"),
        help_text=_(
            "Used by the BMR formula (Mifflin-St Jeor) — a physiological input, not a "
            "gender-identity question."
        ),
    )
    birth_date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        label=_("Date of birth"),
    )
    height = forms.DecimalField(max_digits=6, decimal_places=1, min_value=Decimal("50"))
    weight = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("20"))

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        is_metric = user.unit_system == "metric"
        self.fields["height"].label = _("Height (cm)") if is_metric else _("Height (in)")
        self.fields["weight"].label = _("Current weight (%(unit)s)") % {
            "unit": core_units.weight_unit_label(user.unit_system)
        }
        if user.height is not None:
            display = (
                core_units.meters_to_cm(user.height)
                if is_metric
                else core_units.meters_to_inches(user.height)
            )
            self.initial["height"] = display.quantize(Decimal("0.1"))

    def canonical_height_m(self):
        height = self.cleaned_data["height"]
        is_metric = self.user.unit_system == "metric"
        return core_units.cm_to_meters(height) if is_metric else core_units.inches_to_meters(height)

    def canonical_weight_kg(self):
        return core_units.display_to_kg(self.cleaned_data["weight"], self.user.unit_system)


class ActivityInputsForm(forms.Form):
    activity_job = forms.ChoiceField(choices=ActivityJob.choices, label=_("Your job"))
    daily_steps = forms.IntegerField(
        required=False, min_value=0, label=_("Average daily steps (if you know it)")
    )
    training_sessions_per_week = forms.IntegerField(
        required=False, min_value=0, max_value=14, label=_("Gym sessions per week")
    )
    training_session_minutes = forms.IntegerField(
        required=False, min_value=0, label=_("Typical session length (minutes)")
    )
    other_exercise_minutes_per_week = forms.IntegerField(
        required=False, min_value=0, label=_("Other exercise (minutes/week)")
    )
    self_reported_daily_calories = forms.IntegerField(
        required=False,
        min_value=0,
        label=_("Current daily calories, if you already track this"),
        help_text=_("Used only to compare against this app's own estimate, never as an input."),
    )


class ActivityLevelConfirmForm(forms.Form):
    activity_level = forms.ChoiceField(choices=ActivityLevel.choices, label=_("Activity level"))


class GoalStepForm(forms.Form):
    # x-model/@change: apps.nutrition.energy.DEFAULT_RATE_KG_PER_WEEK is
    # embedded as JSON in the template (see DEFAULT_RATES_JSON_SAFE
    # below) so picking a goal instantly fills in that goal's default
    # rate client-side — no round-trip needed just to see a sensible
    # starting number, matching the rest of this app's Alpine.js usage
    # for small, purely-presentational state.
    goal_type = forms.ChoiceField(
        choices=GoalType.choices,
        label=_("Goal"),
        widget=forms.Select(attrs={"x-model": "goalType", "@change": "rate = rates[goalType]"}),
    )
    target_weight = forms.DecimalField(
        max_digits=8, decimal_places=2, required=False, min_value=Decimal("20")
    )
    target_rate = forms.DecimalField(
        max_digits=5, decimal_places=3, widget=forms.NumberInput(attrs={"x-model": "rate"})
    )

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        unit_label = core_units.weight_unit_label(user.unit_system)
        self.fields["target_weight"].label = _("Target weight (%(unit)s)") % {"unit": unit_label}
        self.fields["target_rate"].label = _("Target rate (kg/week)")
        self.fields["target_rate"].help_text = _(
            "Negative to lose weight, positive to gain. Pre-filled from your goal below — "
            "capped at a safe rate for your bodyweight."
        )

    def canonical_target_weight_kg(self):
        value = self.cleaned_data.get("target_weight")
        if value is None:
            return None
        return core_units.display_to_kg(value, self.user.unit_system)


DEFAULT_RATES_JSON_SAFE = {
    goal_type.value: str(rate) for goal_type, rate in energy.DEFAULT_RATE_KG_PER_WEEK.items()
}


class FoodForm(forms.ModelForm):
    """A user's own custom food — see docs/NUTRITION.md "Food". Every
    nutrition value is *per `serving_size` of `serving_unit`*, not per
    gram — the label makes that explicit rather than assuming it's
    obvious."""

    class Meta:
        from .models import Food

        model = Food
        fields = [
            "name",
            "brand",
            "serving_size",
            "serving_unit",
            "calories",
            "protein_grams",
            "carbohydrate_grams",
            "fat_grams",
            "fiber_grams",
            "sugar_grams",
            "saturated_fat_grams",
            "sodium_mg",
        ]
        help_texts = {
            "serving_size": _(
                "All the nutrition values below are for this amount, in the unit chosen "
                "next to it — e.g. \"100 g\" if the values are per 100 grams."
            ),
        }


class FoodSearchForm(forms.Form):
    q = forms.CharField(required=False, label=_("Search foods"))


class DiaryAddEntryForm(forms.Form):
    """Adds one Food (local or freshly imported from OpenFoodFacts) to
    a specific meal slot on a specific date. Exactly one of `food_id`/
    `off_barcode` is expected — a local pick vs. an OFF search result
    the user is importing on the fly (apps.nutrition.services.
    import_or_refresh_food_from_off)."""

    food_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    off_barcode = forms.CharField(required=False, widget=forms.HiddenInput)
    meal_slot = forms.ModelChoiceField(queryset=None)
    quantity = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))

    def __init__(self, *args, user, **kwargs):
        from . import services

        super().__init__(*args, **kwargs)
        self.fields["meal_slot"].queryset = services.visible_meal_slots(user)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("food_id") and not cleaned.get("off_barcode"):
            raise forms.ValidationError(_("Pick a food to add."))
        return cleaned


class DiaryEntryQuantityForm(forms.ModelForm):
    class Meta:
        from .models import DiaryEntry

        model = DiaryEntry
        fields = ["quantity", "notes"]


class RecipeForm(forms.ModelForm):
    class Meta:
        from .models import Recipe

        model = Recipe
        fields = ["name", "servings", "instructions"]


class RecipeIngredientQuantityForm(forms.ModelForm):
    """Changes only how much of an already-picked ingredient a recipe
    uses — same "quantity-only edit" shape as DiaryEntryQuantityForm
    above. Before this existed, the only way to correct "200g rice"
    to "250g rice" was to delete the ingredient and re-add it through
    the whole search-and-pick flow again, losing its position in the
    list in the process."""

    class Meta:
        from .models import RecipeIngredient

        model = RecipeIngredient
        fields = ["quantity"]


class RecipeIngredientSearchForm(forms.Form):
    """Adds one Food (local or freshly imported from OpenFoodFacts) as
    a recipe ingredient — same shape as DiaryAddEntryForm above, minus
    the meal_slot/date a diary entry needs but an ingredient doesn't.
    Search-and-pick rather than a bare dropdown of foods the user
    already had to create by hand elsewhere: this is the one place a
    recipe's macros come from, so finding/importing the right food
    has to be as easy here as it already is in the food diary."""

    food_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    off_barcode = forms.CharField(required=False, widget=forms.HiddenInput)
    quantity = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("food_id") and not cleaned.get("off_barcode"):
            raise forms.ValidationError(_("Pick a food to add."))
        return cleaned


class LogRecipeForm(forms.Form):
    """Logs N servings of a recipe into the diary — the "diary
    version" of DiaryAddEntryForm, for a recipe instead of a raw
    food. `date` defaults to today but is editable — same as
    LogDietPlanForm, so logging a recipe eaten yesterday (or planned
    for tomorrow) doesn't require leaving this page for a workaround
    that doesn't actually exist elsewhere (the diary's own "add food"
    search only ever offers Food, never a Recipe)."""

    meal_slot = forms.ModelChoiceField(queryset=None, label=_("Meal"))
    quantity = forms.DecimalField(
        max_digits=8, decimal_places=2, min_value=Decimal("0.01"), label=_("Servings")
    )
    date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        label=_("Log for date"),
    )

    def __init__(self, *args, user, **kwargs):
        from django.utils import timezone

        from . import services

        super().__init__(*args, **kwargs)
        self.fields["meal_slot"].queryset = services.visible_meal_slots(user)
        self.fields["date"].initial = timezone.localdate()


class DietPlanForm(forms.Form):
    """Step 1 of the diet builder — see docs/NUTRITION.md "Diet
    builder wizard". Calorie/macro fields are pre-filled from the
    user's active NutritionTarget by the view, not defaulted here
    (this form has no DB access of its own to that), and stay fully
    editable — a plan doesn't have to match the live target exactly."""

    name = forms.CharField(max_length=200, label=_("Plan name"))
    target_calories = forms.IntegerField(min_value=1, label=_("Daily calories"))
    target_protein_grams = forms.DecimalField(
        max_digits=6, decimal_places=2, min_value=Decimal("0"), label=_("Protein (g)")
    )
    target_carbohydrate_grams = forms.DecimalField(
        max_digits=6, decimal_places=2, min_value=Decimal("0"), label=_("Carbohydrates (g)")
    )
    target_fat_grams = forms.DecimalField(
        max_digits=6, decimal_places=2, min_value=Decimal("0"), label=_("Fat (g)")
    )
    meal_slots = forms.ModelMultipleChoiceField(
        queryset=None, widget=forms.CheckboxSelectMultiple, label=_("Which meals?")
    )

    def __init__(self, *args, user, **kwargs):
        from . import services

        super().__init__(*args, **kwargs)
        self.fields["meal_slots"].queryset = services.visible_meal_slots(user)


class DietPlanItemForm(forms.ModelForm):
    """Swapping a single generated item — spec: change one meal or
    food without rebuilding the whole plan."""

    def __init__(self, *args, user, **kwargs):
        from django.db.models import Q

        super().__init__(*args, **kwargs)
        self.fields["food"].queryset = Food.objects.filter(
            Q(owner=user) | Q(owner__isnull=True), active=True
        ).order_by("name")
        self.fields["recipe"].queryset = Recipe.objects.filter(owner=user).order_by("name")
        self.fields["food"].required = False
        self.fields["recipe"].required = False

    def clean(self):
        cleaned = super().clean()
        if bool(cleaned.get("food")) == bool(cleaned.get("recipe")):
            raise forms.ValidationError(
                _("Pick either a food or a recipe, not both or neither.")
            )
        return cleaned

    class Meta:
        from .models import DietPlanItem

        model = DietPlanItem
        fields = ["food", "recipe", "quantity"]


class DietPlanMealItemSearchForm(forms.Form):
    """Adds one extra Food (local or freshly imported from
    OpenFoodFacts) to an already-generated meal — same search-and-pick
    shape as RecipeIngredientSearchForm/DiaryAddEntryForm above. Meal
    planning shouldn't be locked to whatever single item the generator
    originally picked (see apps.nutrition.diet_builder's own
    documented "deliberately simple, not a knapsack solver" scope
    note) — a user building out a real day's plan needs to be able to
    add more to a meal, not just swap its one item."""

    food_id = forms.IntegerField(required=False, widget=forms.HiddenInput)
    off_barcode = forms.CharField(required=False, widget=forms.HiddenInput)
    quantity = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("0.01"))

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("food_id") and not cleaned.get("off_barcode"):
            raise forms.ValidationError(_("Pick a food to add."))
        return cleaned


class LogDietPlanForm(forms.Form):
    date = forms.DateField(
        widget=forms.DateInput(attrs={"type": "date"}, format="%Y-%m-%d"),
        label=_("Log for date"),
    )


class _UnitAwareWeightHeightForm(forms.Form):
    """Shared height/weight-in-the-user's-display-unit plumbing for the
    standalone calculators below — same conversion pattern as
    BodyStepForm above, but pre-fillable from a signed-in user's
    already-known height/weight (nutrition_profile + latest logged
    body weight) rather than session state, since these calculators
    are one-off lookups, not a wizard step being accumulated."""

    def __init__(self, *args, user, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)
        is_metric = user.unit_system == "metric"
        if "height_cm" in self.fields:
            self.fields["height_cm"].label = _("Height (cm)") if is_metric else _("Height (in)")
            if user.height is not None:
                display = (
                    core_units.meters_to_cm(user.height)
                    if is_metric
                    else core_units.meters_to_inches(user.height)
                )
                self.initial.setdefault("height_cm", display.quantize(Decimal("0.1")))
        if "weight_kg" in self.fields:
            self.fields["weight_kg"].label = _("Current weight (%(unit)s)") % {
                "unit": core_units.weight_unit_label(user.unit_system)
            }

    def canonical_height_cm(self):
        value = self.cleaned_data["height_cm"]
        is_metric = self.user.unit_system == "metric"
        meters = core_units.cm_to_meters(value) if is_metric else core_units.inches_to_meters(value)
        return core_units.meters_to_cm(meters)

    def canonical_weight_kg(self):
        return core_units.display_to_kg(self.cleaned_data["weight_kg"], self.user.unit_system)


class BmrTdeeCalculatorForm(_UnitAwareWeightHeightForm):
    """See apps.nutrition.energy.calculate_bmr/calculate_tdee — this
    form only collects the inputs those two existing functions need,
    it does not reimplement the formula."""

    biological_sex = forms.ChoiceField(choices=BiologicalSex.choices, label=_("Biological sex"))
    age_years = forms.IntegerField(min_value=13, max_value=100, label=_("Age (years)"))
    height_cm = forms.DecimalField(max_digits=6, decimal_places=1, min_value=Decimal("50"))
    weight_kg = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("20"))
    activity_level = forms.ChoiceField(choices=ActivityLevel.choices, label=_("Activity level"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        profile = getattr(user, "nutrition_profile", None)
        if profile is not None:
            self.initial.setdefault("biological_sex", profile.biological_sex)
            self.initial.setdefault("age_years", profile.age_years)
            self.initial.setdefault("activity_level", profile.activity_level)


class MacroCalculatorForm(_UnitAwareWeightHeightForm):
    """See apps.nutrition.macros.calculate_macros — same "collect the
    inputs, don't reimplement the math" shape as the form above."""

    weight_kg = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("20"))
    daily_calories = forms.IntegerField(min_value=800, max_value=10000, label=_("Daily calories"))
    goal_type = forms.ChoiceField(choices=GoalType.choices, label=_("Goal"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        profile = getattr(user, "nutrition_profile", None)
        if profile is not None:
            from .models import NutritionGoal, NutritionTarget

            goal = NutritionGoal.objects.filter(user=user, ended_at__isnull=True).first()
            if goal is not None:
                self.initial.setdefault("goal_type", goal.goal_type)
            target = NutritionTarget.objects.filter(user=user, ended_at__isnull=True).first()
            if target is not None:
                self.initial.setdefault("daily_calories", target.daily_calories)


class BodyFatCalculatorForm(_UnitAwareWeightHeightForm):
    """See apps.nutrition.calculators.estimate_body_fat_percent — the
    U.S. Navy circumference method. `hip_cm` is only required for the
    female formula; enforced in clean() rather than a fixed
    `required=True` since which fields are required depends on the
    other field's value."""

    biological_sex = forms.ChoiceField(choices=BiologicalSex.choices, label=_("Biological sex"))
    height_cm = forms.DecimalField(max_digits=6, decimal_places=1, min_value=Decimal("50"))
    neck_cm = forms.DecimalField(
        max_digits=5, decimal_places=1, min_value=Decimal("10"), label=_("Neck circumference")
    )
    waist_cm = forms.DecimalField(
        max_digits=5, decimal_places=1, min_value=Decimal("30"), label=_("Waist circumference")
    )
    hip_cm = forms.DecimalField(
        max_digits=5,
        decimal_places=1,
        min_value=Decimal("30"),
        required=False,
        label=_("Hip circumference"),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        profile = getattr(user, "nutrition_profile", None)
        if profile is not None:
            self.initial.setdefault("biological_sex", profile.biological_sex)
        for field_name in ("neck_cm", "waist_cm", "hip_cm"):
            self.fields[field_name].label = (
                self.fields[field_name].label
                if self.user.unit_system == "metric"
                else f"{self.fields[field_name].label} (in)"
            )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("biological_sex") == BiologicalSex.FEMALE and not cleaned.get("hip_cm"):
            self.add_error(
                "hip_cm", _("Hip circumference is required for the women's formula.")
            )
        return cleaned

    def canonical_neck_cm(self):
        return self._canonical_circumference("neck_cm")

    def canonical_waist_cm(self):
        return self._canonical_circumference("waist_cm")

    def canonical_hip_cm(self):
        if not self.cleaned_data.get("hip_cm"):
            return None
        return self._canonical_circumference("hip_cm")

    def _canonical_circumference(self, field_name):
        value = self.cleaned_data[field_name]
        if self.user.unit_system == "metric":
            return value
        return core_units.meters_to_cm(core_units.inches_to_meters(value))


class WaterIntakeCalculatorForm(_UnitAwareWeightHeightForm):
    """See apps.nutrition.calculators.estimate_daily_water_liters."""

    weight_kg = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("20"))
    activity_level = forms.ChoiceField(choices=ActivityLevel.choices, label=_("Activity level"))

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        profile = getattr(user, "nutrition_profile", None)
        if profile is not None:
            self.initial.setdefault("activity_level", profile.activity_level)


class BMICalculatorForm(_UnitAwareWeightHeightForm):
    """See apps.core.bmi.calculate_bmi — a standalone version of the
    same calculation the Body weight measurement history page already
    shows once a height is on file (docs/DOMAIN_MODEL.md "User"), for
    anyone who wants the number without logging a weight first."""

    height_cm = forms.DecimalField(max_digits=6, decimal_places=1, min_value=Decimal("50"))
    weight_kg = forms.DecimalField(max_digits=8, decimal_places=2, min_value=Decimal("20"))


class WaistHipRatioCalculatorForm(_UnitAwareWeightHeightForm):
    """See apps.nutrition.calculators.calculate_waist_hip_ratio. Same
    shape as BodyFatCalculatorForm's own circumference fields —
    entered in the user's display unit, converted to canonical cm for
    the calculation, same as everywhere else circumferences are
    logged in this app (docs/DOMAIN_MODEL.md "BodyMeasurement")."""

    biological_sex = forms.ChoiceField(choices=BiologicalSex.choices, label=_("Biological sex"))
    waist_cm = forms.DecimalField(
        max_digits=5, decimal_places=1, min_value=Decimal("30"), label=_("Waist circumference")
    )
    hip_cm = forms.DecimalField(
        max_digits=5, decimal_places=1, min_value=Decimal("30"), label=_("Hip circumference")
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        profile = getattr(user, "nutrition_profile", None)
        if profile is not None:
            self.initial.setdefault("biological_sex", profile.biological_sex)
        for field_name in ("waist_cm", "hip_cm"):
            self.fields[field_name].label = (
                self.fields[field_name].label
                if self.user.unit_system == "metric"
                else f"{self.fields[field_name].label} (in)"
            )

    def canonical_waist_cm(self):
        return self._canonical_circumference("waist_cm")

    def canonical_hip_cm(self):
        return self._canonical_circumference("hip_cm")

    def _canonical_circumference(self, field_name):
        value = self.cleaned_data[field_name]
        if self.user.unit_system == "metric":
            return value
        return core_units.meters_to_cm(core_units.inches_to_meters(value))


class TimeToGoalCalculatorForm(_UnitAwareWeightHeightForm):
    """See apps.nutrition.calculators.estimate_weeks_to_goal. A raw
    signed weekly rate, not a GoalType dropdown like the onboarding
    goal step uses — this is a flexible what-if tool ("at *this*
    rate, how long"), not tied to this app's own goal-type rate
    presets."""

    current_weight_kg = forms.DecimalField(
        max_digits=8, decimal_places=2, min_value=Decimal("20"), label=_("Current weight (kg)")
    )
    target_weight_kg = forms.DecimalField(
        max_digits=8, decimal_places=2, min_value=Decimal("20"), label=_("Target weight (kg)")
    )
    rate_kg_per_week = forms.DecimalField(
        max_digits=5,
        decimal_places=3,
        label=_("Rate (kg/week — negative to lose, positive to gain)"),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, user=user, **kwargs)
        is_metric = user.unit_system == "metric"
        if not is_metric:
            unit_label = core_units.weight_unit_label(user.unit_system)
            self.fields["current_weight_kg"].label = _("Current weight (%(unit)s)") % {
                "unit": unit_label
            }
            self.fields["target_weight_kg"].label = _("Target weight (%(unit)s)") % {
                "unit": unit_label
            }
            self.fields["rate_kg_per_week"].label = _(
                "Rate (%(unit)s/week — negative to lose, positive to gain)"
            ) % {"unit": unit_label}

    def canonical_current_weight_kg(self):
        return core_units.display_to_kg(
            self.cleaned_data["current_weight_kg"], self.user.unit_system
        )

    def canonical_target_weight_kg(self):
        return core_units.display_to_kg(
            self.cleaned_data["target_weight_kg"], self.user.unit_system
        )

    def canonical_rate_kg_per_week(self):
        return core_units.display_to_kg(
            self.cleaned_data["rate_kg_per_week"], self.user.unit_system
        )
