"""Energy expenditure and calorie-target calculation — see
docs/NUTRITION.md "Energy calculation" and "Safety bounds" for the full
reasoning behind every formula/constant here. Pure functions, no
Django DB/HTTP dependency, same shape as apps.core.bmi.

Every number this module produces is an *estimate*. Callers must
present them as such ("estimated," "recommended," "target" — never
"you burn exactly X") — see docs/NUTRITION.md's own opening goal.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

# Eager gettext, not gettext_lazy (unlike apps.core.bmi's BMI_CATEGORIES
# or apps.progression.engine's reason strings): every translated string
# here is built and consumed entirely within one function call — most
# get joined together with str.join(), which a lazy proxy doesn't
# support — never stashed at module/class load time, so laziness would
# buy nothing.
from django.utils.translation import gettext as _

from .models import ActivityLevel, BiologicalSex, GoalType

CALORIE_PLACES = Decimal("1")  # whole kcal — no false precision.

# The standard approximation this module's rate<->calorie conversions
# are built on. An approximation, not a law of physics — real weight
# change also involves water retention, metabolic adaptation, and body
# composition shifts, which is exactly why the dynamic-adjustment
# engine (apps.nutrition.trends/suggestions) exists: to correct for
# reality diverging from this estimate over time.
KCAL_PER_KG_BODY_FAT = Decimal("7700")

ACTIVITY_MULTIPLIERS = {
    ActivityLevel.SEDENTARY: Decimal("1.2"),
    ActivityLevel.LIGHT: Decimal("1.375"),
    ActivityLevel.MODERATE: Decimal("1.55"),
    ActivityLevel.ACTIVE: Decimal("1.725"),
    ActivityLevel.VERY_ACTIVE: Decimal("1.9"),
}

# Pre-fills goal-setting; always user-editable within the safety caps
# below.
DEFAULT_RATE_KG_PER_WEEK = {
    GoalType.FAT_LOSS_CONSERVATIVE: Decimal("-0.25"),
    GoalType.FAT_LOSS_MODERATE: Decimal("-0.5"),
    GoalType.FAT_LOSS_AGGRESSIVE: Decimal("-0.75"),
    GoalType.MAINTENANCE: Decimal("0"),
    GoalType.MUSCLE_GAIN_LEAN: Decimal("0.125"),
    GoalType.MUSCLE_GAIN_MODERATE: Decimal("0.25"),
    GoalType.MUSCLE_GAIN_AGGRESSIVE: Decimal("0.5"),
}

# Safety caps as a fraction of current bodyweight/week — scales
# correctly across body sizes, unlike a flat kg number.
MAX_FAT_LOSS_RATE_FRACTION = Decimal("0.01")  # 1% bodyweight/week
MAX_MUSCLE_GAIN_RATE_FRACTION = Decimal("0.005")  # 0.5% bodyweight/week

# Classical clinical minimum-intake figures, raised further for anyone
# whose BMR alone is already close to or above them.
ABSOLUTE_CALORIE_FLOOR = {
    BiologicalSex.MALE: Decimal("1500"),
    BiologicalSex.FEMALE: Decimal("1200"),
}
BMR_FLOOR_FRACTION = Decimal("0.9")


def calculate_bmr(
    weight_kg: Decimal, height_cm: Decimal, age_years: int, biological_sex: str
) -> Decimal:
    """Mifflin-St Jeor — chosen over Harris-Benedict (less accurate
    against modern population data) and Katch-McArdle (needs a
    body-fat % this app has no reliable way to measure). See
    docs/NUTRITION.md "BMR"."""
    base = (
        Decimal("10") * weight_kg
        + Decimal("6.25") * height_cm
        - Decimal("5") * Decimal(age_years)
    )
    bmr = base + Decimal("5") if biological_sex == BiologicalSex.MALE else base - Decimal("161")
    return bmr.quantize(CALORIE_PLACES, rounding=ROUND_HALF_UP)


def calculate_tdee(bmr: Decimal, activity_level: str) -> Decimal:
    return (bmr * ACTIVITY_MULTIPLIERS[activity_level]).quantize(
        CALORIE_PLACES, rounding=ROUND_HALF_UP
    )


@dataclass(frozen=True)
class ActivityLevelSuggestion:
    activity_level: str
    reason: str


def suggest_activity_level(
    *,
    activity_job: str,
    daily_steps: int | None = None,
    training_sessions_per_week: int | None = None,
    other_exercise_minutes_per_week: int | None = None,
) -> ActivityLevelSuggestion:
    """Scores the concrete, answerable onboarding inputs into one of
    the five standard TDEE buckets, with a one-line explanation — see
    docs/NUTRITION.md "Choosing an activity level" for why this is
    suggested instead of asked directly. The score is a simple sum of
    independent signals (job, steps, training frequency, other
    exercise), each contributing 0-4 points; the bucket boundaries
    were chosen so a fully sedentary profile scores 0 and a fully
    active one across every signal scores at the top of Very active,
    with the three middle buckets evenly spaced between."""
    reasons = []
    score = 0

    job_points = {"sedentary": 0, "light": 1, "moderate": 2, "physical": 3}[activity_job]
    score += job_points
    if job_points:
        reasons.append(_("a %(job)s job") % {"job": activity_job.replace("_", " ")})

    if daily_steps is not None:
        if daily_steps >= 12500:
            step_points = 4
        elif daily_steps >= 10000:
            step_points = 3
        elif daily_steps >= 7500:
            step_points = 2
        elif daily_steps >= 5000:
            step_points = 1
        else:
            step_points = 0
        score += step_points
        reasons.append(_("~%(steps)s steps/day") % {"steps": daily_steps})

    if training_sessions_per_week:
        if training_sessions_per_week >= 7:
            training_points = 4
        elif training_sessions_per_week >= 5:
            training_points = 3
        elif training_sessions_per_week >= 3:
            training_points = 2
        else:
            training_points = 1
        score += training_points
        reasons.append(
            _("%(count)s gym sessions a week") % {"count": training_sessions_per_week}
        )

    if other_exercise_minutes_per_week:
        if other_exercise_minutes_per_week > 300:
            other_points = 4
        elif other_exercise_minutes_per_week > 180:
            other_points = 3
        elif other_exercise_minutes_per_week > 90:
            other_points = 2
        else:
            other_points = 1
        score += other_points
        reasons.append(_("other regular exercise"))

    if score <= 2:
        level = ActivityLevel.SEDENTARY
    elif score <= 5:
        level = ActivityLevel.LIGHT
    elif score <= 8:
        level = ActivityLevel.MODERATE
    elif score <= 11:
        level = ActivityLevel.ACTIVE
    else:
        level = ActivityLevel.VERY_ACTIVE

    if reasons:
        reason = _("Suggested: %(label)s — %(factors)s") % {
            "label": ActivityLevel(level).label,
            "factors": ", ".join(reasons),
        }
    else:
        reason = _("Suggested: %(label)s — no activity details given yet") % {
            "label": ActivityLevel(level).label
        }
    return ActivityLevelSuggestion(activity_level=level, reason=reason)


def max_safe_rate_kg_per_week(weight_kg: Decimal, goal_type: str) -> Decimal:
    """The rate cap for this goal's direction, scaled to current
    bodyweight. Signed the same way a real rate is: negative for fat
    loss, positive for muscle gain, zero for maintenance."""
    if goal_type == GoalType.MAINTENANCE:
        return Decimal("0")
    if goal_type.startswith("fat_loss"):
        return -(weight_kg * MAX_FAT_LOSS_RATE_FRACTION)
    return weight_kg * MAX_MUSCLE_GAIN_RATE_FRACTION


def clamp_rate(weight_kg: Decimal, goal_type: str, target_rate_kg_per_week: Decimal) -> Decimal:
    """`target_rate_kg_per_week`, capped to `max_safe_rate_kg_per_week`
    — a fat-loss rate can never be *more negative* than the cap, a
    muscle-gain rate never *more positive* than it."""
    cap = max_safe_rate_kg_per_week(weight_kg, goal_type)
    if goal_type == GoalType.MAINTENANCE:
        return Decimal("0")
    if goal_type.startswith("fat_loss"):
        return max(target_rate_kg_per_week, cap)
    return min(target_rate_kg_per_week, cap)


def calorie_floor(
    weight_kg: Decimal, height_cm: Decimal, age_years: int, biological_sex: str
) -> Decimal:
    """`max(sex-based clinical minimum, 90% of BMR)` — see
    docs/NUTRITION.md "Safety bounds" for why both caps exist
    independently."""
    bmr = calculate_bmr(weight_kg, height_cm, age_years, biological_sex)
    return max(ABSOLUTE_CALORIE_FLOOR[biological_sex], bmr * BMR_FLOOR_FRACTION)


@dataclass(frozen=True)
class CalorieTargetReasonData:
    """Every number `render_calorie_target_reason` needs, snapshotted
    at calculation time — stored on NutritionTarget (see
    apps.nutrition.models) so a *current* target's explanation can be
    re-rendered in whichever language is active whenever it's shown,
    instead of freezing at whatever language happened to be active the
    moment it was first calculated. Deliberately just the pieces the
    sentences below actually branch on, not a dump of every local
    variable calculate_calorie_target touches."""

    tdee: int
    capped_rate: Decimal
    rate_was_capped: bool
    rate_cap_fraction_percent: Decimal
    raw_calories: int
    floor: int
    floor_was_applied: bool


def render_calorie_target_reason(data: CalorieTargetReasonData, final_calories: int) -> str:
    """Builds the human-readable explanation from already-computed
    numbers, in whatever language is active right now. The one place
    this sentence gets assembled — called both immediately after
    `calculate_calorie_target` (e.g. the onboarding review step's
    preview, before anything is even saved) and again on every later
    view of a still-current NutritionTarget
    (NutritionTarget.display_reason), so the wording always matches
    the viewer's current language rather than whatever was active when
    the target was first calculated."""
    parts = [_("Estimated maintenance (TDEE): %(tdee)s kcal/day.") % {"tdee": data.tdee}]
    if data.rate_was_capped:
        parts.append(
            str(
                _(
                    "Your requested rate was reduced to a safer %(rate)s kg/week "
                    "(capped at %(fraction)s%% of bodyweight/week)."
                )
            )
            % {"rate": data.capped_rate, "fraction": data.rate_cap_fraction_percent}
        )
    if data.floor_was_applied:
        parts.append(
            str(
                _(
                    "Your target rate would need %(raw)s kcal/day, below a safe "
                    "minimum — capped at %(floor)s kcal/day."
                )
            )
            % {"raw": data.raw_calories, "floor": data.floor}
        )
    else:
        parts.append(
            _("Target: %(rate)s kg/week → %(calories)s kcal/day.")
            % {"rate": data.capped_rate, "calories": final_calories}
        )
    return " ".join(parts)


@dataclass(frozen=True)
class CalorieTargetResult:
    daily_calories: int
    reason: str
    reason_data: CalorieTargetReasonData
    rate_was_capped: bool
    floor_was_applied: bool


def calculate_calorie_target(
    *,
    tdee: Decimal,
    weight_kg: Decimal,
    height_cm: Decimal,
    age_years: int,
    biological_sex: str,
    goal_type: str,
    target_rate_kg_per_week: Decimal,
) -> CalorieTargetResult:
    """The full goal → calorie-target pipeline: cap the requested rate
    to a safe bound, derive a calorie delta from the (possibly capped)
    rate via `KCAL_PER_KG_BODY_FAT`, then clamp the result to the
    absolute floor. Either clamp can fire independently — both are
    reported so the caller can explain exactly what happened."""
    capped_rate = clamp_rate(weight_kg, goal_type, target_rate_kg_per_week)
    rate_was_capped = capped_rate != target_rate_kg_per_week

    daily_delta = capped_rate * KCAL_PER_KG_BODY_FAT / Decimal("7")
    raw_calories = tdee + daily_delta

    floor = calorie_floor(weight_kg, height_cm, age_years, biological_sex)
    floor_was_applied = raw_calories < floor
    final_calories = max(raw_calories, floor).quantize(CALORIE_PLACES, rounding=ROUND_HALF_UP)

    reason_data = CalorieTargetReasonData(
        tdee=int(tdee),
        capped_rate=capped_rate,
        rate_was_capped=rate_was_capped,
        rate_cap_fraction_percent=(
            MAX_FAT_LOSS_RATE_FRACTION
            if goal_type.startswith("fat_loss")
            else MAX_MUSCLE_GAIN_RATE_FRACTION
        )
        * 100,
        raw_calories=int(raw_calories),
        floor=int(floor),
        floor_was_applied=floor_was_applied,
    )

    return CalorieTargetResult(
        daily_calories=int(final_calories),
        reason=render_calorie_target_reason(reason_data, int(final_calories)),
        reason_data=reason_data,
        rate_was_capped=rate_was_capped,
        floor_was_applied=floor_was_applied,
    )
