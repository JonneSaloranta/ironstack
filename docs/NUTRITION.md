# Nutrition System

## Goal

Answer, for any user, at any time:

> "How much should I eat, what should I eat, and how do I know if it's
> working?"

Covers energy expenditure estimation, goal-based calorie/macro
targets, a food diary with meals and recipes, a guided diet-plan
builder, and — the feature that makes this more than a calculator —
turning the user's *actual* logged weight trend into an explainable,
never-auto-applied calorie adjustment suggestion.

This is domain logic, same rule as `docs/PROGRESSION.md`: **independent
of views/templates**, testable in isolation, and every number-producing
function must be able to explain itself. Nutrition tracking was
explicitly out of scope through v1 (`docs/PRODUCT_REQUIREMENTS.md` said
"do not implement unless explicitly requested") — it has now been
requested, so this document, and the `apps/nutrition` app it describes,
supersede that line.

## Why a new app, not an extension of `apps/measurements`

`apps/measurements` already owns body-weight tracking (`BodyMeasurement`
under the system "Body weight" `MeasurementType`) — nutrition **reads
that**, it does not duplicate it. A user's target weight is a plain
scalar on a goal, not a new time series.

Everything else here — food, recipes, diary entries, calorie/macro
targets, diet plans — is a genuinely different domain (nutrition, not
anthropometry) with its own lifecycle and its own history requirements.
Folding it into `apps/measurements` would make that app's one clear job
("a time-stamped reading of a measurement type") do two unrelated
things. A new `apps/nutrition` app, following the exact same
conventions as every other app here (`models.py` for data,
`services.py`/small focused modules for pure domain logic, no logic in
views or templates), is the smaller, more consistent change.

## Domain model

All weight/energy values are `Decimal`, never `float` (project-wide
rule, `docs/ARCHITECTURE.md` "Units and precision"). Calories are
always kcal — no display-unit conversion needed, unlike weight/length,
so no `unit_kind`-style dispatch layer is required for energy itself.

### `NutritionProfile` — current facts, one row per user

The physiological/lifestyle inputs the calorie engine needs. A
`OneToOneField` to `User`, not more fields bolted onto `User` itself —
`accounts` already carries display preferences (`height`, `unit_system`,
...); this is a different, nutrition-specific concern, kept in its own
app the same way `apps.measurements` doesn't live inside `apps.accounts`
either.

- `user` (`OneToOneField`)
- `biological_sex` — `male`/`female` (the Mifflin-St Jeor constant is a
  physiological input, not a gender-identity field; labelled precisely
  so it's clear why it's being asked)
- `birth_date` — stored, not raw `age`, so it never silently goes stale
  the way a once-entered "age" would; age is computed on read
- `activity_job` — `sedentary` / `light` / `moderate` / `physical`
  (desk job vs. on-your-feet vs. manual labour)
- `daily_steps` — optional `PositiveIntegerField`
- `training_sessions_per_week`, `training_session_minutes` — optional
  ints, the user's own estimate (later phases can cross-check this
  against real `apps.workouts` history — see "Training integration")
- `other_exercise_minutes_per_week` — optional int (cardio, sport, ...)
- `activity_level` — the actual TDEE-multiplier bucket
  (`sedentary`/`light`/`moderate`/`active`/`very_active`). **Not asked
  directly** — see "Choosing an activity level" below for why, and how
  it's suggested instead of self-reported.
- `self_reported_daily_calories` — optional int, "if you already track
  this, tell us" (section 3 of the spec) — used only as a secondary
  data point the dashboard can compare its own estimate against, never
  as an input to the estimate itself
- `created_at`, `updated_at`

No history needed on this model itself — it's inputs, not an output
a user would want to compare month-to-month the way a *target* is
(see `NutritionGoal`/`NutritionTarget` below). If the user gets a year
older or changes jobs, the old birth_date/job value was never a
"decision" worth preserving — it's just a fact that update in place,
same as `User.height`.

### `NutritionGoal` — the user's stated intent, historized

```
user, goal_type, target_weight (nullable, kg),
target_rate_kg_per_week (signed Decimal), started_at, ended_at (nullable),
notes
```

`goal_type` choices: `fat_loss_aggressive`, `fat_loss_moderate`,
`fat_loss_conservative`, `maintenance`, `muscle_gain_lean`,
`muscle_gain_moderate`, `muscle_gain_aggressive`.

Setting a new goal never overwrites the old one — it stamps `ended_at`
on whichever goal was previously open (`ended_at IS NULL`) and inserts
a new row. This is the same "append, don't mutate" shape as
`PersonalRecord` (`apps.records`), just for a *stated intent* rather
than an *achievement*. Directly satisfies the spec's history
requirement (section 19): "August: 2500 kcal, September: 2350 kcal" is
recoverable by querying goals/targets ordered by `started_at`, never by
overwriting a single row.

### `NutritionTarget` — the derived, numeric output, historized

```
user, goal (FK), daily_calories, protein_grams, carbohydrate_grams,
fat_grams, source, reason, started_at, ended_at (nullable)
```

`source` choices: `calculated` (straight from the goal via the energy
engine), `manual` (user typed a number directly), `adjusted` (the user
accepted a dynamic-adjustment suggestion — see below).

**Deliberately one model, not the example list's separate
`CalorieTarget`/`MacroTarget`.** Calories and macros are always set as
one coherent unit (macros are computed *from* the calorie figure) and
the spec asks for them to be historized *together* ("Sama koskee:
makroja, painotavoitteita...") — a single historized row per change
keeps that atomic instead of needing two tables to always stay in sync.

Same append/supersede shape as `NutritionGoal`. A goal change *and* a
dynamic adjustment both create a new `NutritionTarget` row; only a goal
change also creates a new `NutritionGoal` row. The "current" target for
a user is simply `NutritionTarget.objects.filter(user=user,
ended_at__isnull=True).first()` — same query shape as `NutritionGoal`'s
"current goal" and `WorkoutSession`'s "in-progress session," so nothing
new to learn.

### `MealSlot` — named diary categories, per-user + system defaults

```
name, order, owner (nullable FK), active
```

Exactly `apps.measurements.MeasurementType`'s pattern reused verbatim:
`owner=None` rows are system-seeded defaults (Breakfast, Lunch, Dinner,
Evening snack — a migration data seed, same shape as measurement types'
0002 seed), a user can add their own (`owner=user`), soft-deactivate
(`active=False`) rather than hard-delete so historical diary entries
stay attached to a real row. Satisfies section 9 exactly: sensible
defaults, user can rename/add.

### `Food`

```
owner (nullable FK), name, brand (optional), serving_size, serving_unit,
calories, protein_grams, carbohydrate_grams, fat_grams,
fiber_grams / sugar_grams / saturated_fat_grams / sodium_mg (all
optional/nullable), active
```

`serving_unit` choices: `g`, `ml`, `piece` — precise mass/volume for
most foods, a count unit for things like "1 egg" where a gram weight
isn't how anyone thinks about it. All nutrition fields are *per
`serving_size` of `serving_unit`* — e.g. "100 g → 165 kcal" for
chicken breast, not "per gram." `owner` nullable, matching
`MeasurementType` again: every v1 food is user-created
(`owner=request.user`), but leaving the column nullable now means a
future shared/imported food library (a barcode/nutrition-database
integration, explicitly flagged in the spec as "don't add it now, but
don't make it hard to add later") can populate `owner=None` rows
without a schema change. No such integration is being added in this
pass — no new dependency, per CLAUDE.md's "before adding a dependency,
check whether existing code already solves the problem," and here
nothing needs one yet.

### `Recipe` / `RecipeIngredient`

```
Recipe: owner, name, servings, instructions (optional)
RecipeIngredient: recipe (FK), food (FK), quantity, order
```

`quantity` is in the *same unit* as `food.serving_unit` — nutrition for
that line is `food`'s per-serving values scaled by
`quantity / food.serving_size`. One shared pure function,
`nutrition.services.scale_nutrition(food, quantity)`, computes this —
reused identically by `RecipeIngredient` totals and by a `DiaryEntry`
logging raw food directly, so there is exactly one place this maths
lives.

A recipe's total nutrition is the sum of its ingredients' scaled
nutrition; per-serving is that total divided by `servings`.

### `DiaryEntry` — one logged item

```
user, date (DateField — the day it counts toward, not when it was
typed), meal_slot (FK), food (nullable FK) XOR recipe (nullable FK),
quantity, logged_at (DateTimeField, default now), notes
```

Exactly one of `food`/`recipe` must be set — enforced with a
`CheckConstraint`, not just convention. `quantity` means grams/ml/pieces
for a food entry, servings for a recipe entry. `date` vs. `logged_at`
mirrors `ExerciseSet.performed_at` vs. `created_at`: the diary date can
be legitimately back-dated (logging breakfast at lunchtime, or
catching up on yesterday), the audit timestamp cannot.

There is deliberately **no separate "Meal" model** distinct from
`Recipe` — the spec's "a meal can have multiple foods, a recipe, a
serving size, nutrition values" describes exactly what a group of
`DiaryEntry` rows sharing one `(date, meal_slot)` already *is*, computed
live (sum of each entry's scaled nutrition), the same "derive, don't
store a duplicate total" rule `apps.analytics` uses everywhere. A
`Recipe` already covers "a named, reusable combination of foods" for
the case where a user wants to log the same combination repeatedly
(section 9's own example, "Chicken Rice Bowl," is a `Recipe`). Two
models for the same idea would just be two places the same bug could
diverge.

### `DietPlan` / `DietPlanMeal` / `DietPlanItem` — the diet-builder's saved output

```
DietPlan: user, name, goal (nullable FK), target_calories,
  target_protein_grams, target_carbohydrate_grams, target_fat_grams,
  created_at, is_active
DietPlanMeal: diet_plan (FK), meal_slot (FK, PROTECT), target_calories,
  order
DietPlanItem: diet_plan_meal (FK), food (nullable FK) XOR recipe
  (nullable FK), quantity, order
```

A plan snapshots the targets it was built against (immutable — a past
plan stays interpretable even after the user's live targets change).
`is_active` marks the one plan currently surfaced on the dashboard;
older plans are kept, not deleted (a user should be able to look back
at "what was I eating during my last cut"). "Log today's plan"
(section 12/13) is one service call that materializes each
`DietPlanItem` into a real `DiaryEntry` for the chosen date — the plan
itself is never mutated by logging it, so it can be reused across many
days.

## Energy calculation

`apps/nutrition/energy.py` — small, pure, `Decimal`-only functions, no
Django/DB/HTTP dependency, same shape as `apps/core/bmi.py`.

### BMR — Mifflin-St Jeor

Chosen over Harris-Benedict (older, less accurate against modern
population data) and Katch-McArdle (needs body-fat %, which this app
doesn't reliably have — no scale/DEXA integration). Mifflin-St Jeor is
the formula most current sports-nutrition guidance treats as the best
general-population default, needs only weight/height/age/sex, and is
transparent enough to show its own working.

```
male:   BMR = 10×weight_kg + 6.25×height_cm − 5×age + 5
female: BMR = 10×weight_kg + 6.25×height_cm − 5×age − 161
```

### Choosing an activity level — suggested, not self-reported

Asking a user to self-classify as "moderately active" is exactly the
kind of ungrounded input `docs/SMART_SUGGESTIONS.md` warns against —
people are bad at it, and it silently becomes a black-box multiplier
with no explanation. Instead, `NutritionProfile`'s granular answers
(`activity_job`, `daily_steps`, `training_sessions_per_week`,
`other_exercise_minutes_per_week`) feed a small scoring function,
`suggest_activity_level(profile)`, that proposes one of the five
standard buckets **with a one-line reason** ("Suggested: Moderately
active — a light-activity job, ~7,000 steps/day, and 3 gym sessions a
week"). The onboarding wizard pre-fills `activity_level` with that
suggestion; the user can override it directly before continuing — same
"pre-filled, editable, explained, never forced" shape the weight
suggestion engine already established.

Standard multipliers, applied to the *confirmed* `activity_level`:

| Level | Multiplier |
|---|---|
| Sedentary | 1.2 |
| Light | 1.375 |
| Moderate | 1.55 |
| Active | 1.725 |
| Very active | 1.9 |

`TDEE = BMR × multiplier`.

### Calorie target from a goal

The user picks a `goal_type`; each sub-level pre-fills a default
`target_rate_kg_per_week`, editable within safety bounds (below):

| goal_type | default rate |
|---|---|
| fat_loss_conservative | −0.25 kg/week |
| fat_loss_moderate | −0.5 kg/week |
| fat_loss_aggressive | −0.75 kg/week |
| maintenance | 0 |
| muscle_gain_lean | +0.125 kg/week |
| muscle_gain_moderate | +0.25 kg/week |
| muscle_gain_aggressive | +0.5 kg/week |

A rate, not a raw calorie delta, is the thing the user actually has an
intuition for ("lose half a kilo a week") — the calorie figure is
*derived* from it, using the standard approximation that 1 kg of body
fat represents ≈7,700 kcal:

```
daily_calorie_delta = target_rate_kg_per_week × 7700 / 7
daily_calories = TDEE + daily_calorie_delta
```

This is an approximation, not a law of physics (metabolic adaptation,
water-weight noise, and body-composition changes all mean the real
number drifts) — which is exactly why section 6's dynamic adjustment
exists: it corrects for reality diverging from this estimate, instead
of trusting the formula forever.

### Safety bounds — enforced, not just suggested

Two independent caps, both applied before a target is ever saved:

1. **Rate cap, as % of current bodyweight/week** (scales correctly
   across body sizes, unlike a flat kg number): fat loss capped at
   1%/week, muscle gain capped at 0.5%/week — both widely-cited
   sports-nutrition upper bounds for a rate that doesn't risk excess
   muscle loss (cutting) or excess fat gain (bulking).
2. **Absolute calorie floor**: `max(1500 if male else 1200, 0.9 × BMR)`
   — the classical clinical minimum-intake figures, raised further for
   anyone whose BMR alone is already close to or above that (a very
   large/muscular person's safe floor is higher than a generic
   number).

If the user's chosen rate would compute a target below the floor, the
target is clamped to the floor and the UI says so plainly, in the
terms section 20 requires: "estimated," "recommended," never "you burn
exactly X." The calorie target is never presented as a diagnosis.

## Macros

`apps/nutrition/macros.py` — `calculate_macros(weight_kg,
daily_calories, goal_type, *, protein_g_per_kg=None, fat_percent=None)`.

Defaults (both overridable — configurable per spec section 7):

- **Protein**: g/kg bodyweight, by goal — 2.2 g/kg for any fat-loss
  level (higher protein spares lean mass in a deficit), 2.0 g/kg for
  any muscle-gain level, 1.8 g/kg for maintenance.
- **Fat**: 25% of total daily calories by default.
- **Carbohydrate**: whatever's left —
  `(daily_calories − protein_kcal − fat_kcal) / 4`, floored at zero. If
  protein+fat alone would exceed the calorie target (only possible at
  an unusually low calorie target with high protein), fat's share is
  scaled down proportionally rather than letting carbs go negative,
  with the same kind of explicit notice as the safety-floor clamp
  above.

Returns grams, kcal, and percent-of-total for each macro — the display
shape section 7 asks for directly. Not locked to one algorithm: the
function's keyword overrides mean a future "high-carb" or "keto"
preset is a new caller, not a rewrite.

## Dynamic calorie adjustment — the weight-trend engine

`apps/nutrition/trends.py` (moving-average/trend math, pure) +
`apps/nutrition/suggestions.py` (the suggestion engine, mirroring
`apps.progression.suggestions`'s exact shape: a frozen dataclass, a
single public `suggest_calorie_adjustment(user)` entry point, no
persisted model — recomputed live every time, same "derive, don't
cache" rule as everywhere else in this codebase).

1. Read the user's logged weight from `apps.measurements`
   (`BodyMeasurement` under the system "Body weight" type) — nutrition
   never keeps its own copy of this data.
2. Bucket readings by day (averaging same-day multiples), then compute
   a **7-day moving average** trend line — never react to a single
   reading, exactly the principle section 6 states outright and
   `docs/PROGRESSION.md` states for training ("failure is a signal, not
   a command").
3. Require a minimum of 14 days' span and at least 4 distinct
   measurement days before attempting a suggestion at all — otherwise
   return an explicit `INSUFFICIENT_DATA` result, the same escape hatch
   `apps.progression.engine.ProgressionAction` already has for exactly
   this situation ("don't pretend to have confidence when there isn't
   enough history").
4. `actual_rate_kg_per_week` = the moving-average trend's slope over
   the available window. Compare against the active goal's
   `target_rate_kg_per_week`.
5. If the two are within a tolerance band (±30% of the target rate's
   own magnitude, with an absolute floor of ±0.1 kg/week so a
   near-zero maintenance target isn't impossibly strict), the result is
   `ON_TRACK` — no adjustment suggested.
6. Otherwise, convert the shortfall into a calorie suggestion via the
   same 7,700 kcal/kg approximation used for the initial target,
   rounded to the nearest 25 kcal (false precision — "-137 kcal/day" —
   is exactly what section 20 warns against), and **capped at ±250
   kcal per suggestion** even if the raw math implies more: a big
   single jump risks over-correcting off one trend estimate, so a
   larger gap gets corrected over more than one adjustment cycle
   instead — the same "don't overreact" philosophy as progression's
   two-consecutive-failures-before-deload rule, applied here as
   one-suggestion-at-a-time instead of a single big swing.
7. Confidence (`LOW`/`MEDIUM`/`HIGH` only — never a numeric score, per
   `docs/SMART_SUGGESTIONS.md`) scales with how much trend data backs
   the suggestion: `LOW` right at the minimum window, `MEDIUM` for a
   few weeks of consistent logging, `HIGH` for a long, low-variance
   trend.
8. `reason` is a plain sentence built the same way
   `apps.progression.engine`'s branches build theirs — e.g. "Target:
   −0.5 kg/week. Actual trend: −0.15 kg/week over the last 3 weeks.
   Suggested adjustment: −150 kcal/day," directly matching the spec's
   own example.

**Never auto-applied.** The dashboard shows it as a dismissible card;
accepting it creates a new `NutritionTarget` (`source="adjusted"`)
carrying the same `reason` forward into the history; dismissing it
just leaves the current target untouched and the suggestion is simply
recomputed (and can change) next time the dashboard is viewed.

## Integration with existing apps

- **`apps.measurements`** — the only source of weight-history data; no
  duplicate weight log inside nutrition (see "Why a new app" above).
- **`apps.workouts`** — a day counts as a "training day" if it has ≥1
  `WorkoutSession` with `status=COMPLETED` and that `started_at__date`
  — the exact filter `apps.analytics.services` already uses. **Not**
  built as a hard training-day/rest-day calorie split in this pass: the
  spec explicitly says not to force this if the data doesn't support it
  reliably, and a fresh user has no session history yet to build it
  from. Training-day awareness starts as a dashboard label ("Training
  day" / "Rest day," informational) with real session data behind it;
  a distinct training-day calorie *target* is a natural, low-risk
  follow-up once that's live and proven useful, not a v1 requirement.
- **`apps.progression`/`apps.records`** — no direct dependency, but
  every architectural pattern here (frozen-dataclass results,
  confidence enum, explicit reason strings, derive-don't-cache,
  append-don't-mutate for history) is deliberately copied from them,
  for the reasons `docs/SMART_SUGGESTIONS.md`/`docs/PR_SYSTEM.md`
  already give.
- **`apps.accounts`** — reuses `User.unit_system` for weight display
  (BodyMeasurement's own conversion layer) but **does not** touch the
  existing account `OnboardingForm`/modal. Nutrition's own onboarding
  (age/sex/height/weight/activity/goal — a much longer flow) is a
  separate, dedicated wizard reachable from the Nutrition section
  itself, not bolted onto the global first-login modal. Firing two
  unrelated onboarding flows back-to-back at first login would be
  exactly the kind of "asking for the sake of asking" section 3
  explicitly warns against for a user who may not even want nutrition
  tracking yet.
- **`apps.api`** — a new `ApiContext.NUTRITION` value, one
  `OwnedResourceViewSet`-based viewset per top-level resource
  (`Food`, `Recipe`, `DiaryEntry`, `NutritionGoal`, `NutritionTarget`),
  serializers always in canonical kcal/grams (never display-converted,
  matching every other context) — no new architecture, just
  `apps/nutrition` following `docs/API.md`'s existing contract.

## Navigation

The bottom nav currently has exactly 5 fixed items (Home, Progress,
Workout, Programs, Profile — `templates/base.html`, guarded by
`apps.core.tests.BottomNavTests`). Nutrition becomes a **6th item**,
not a replacement for any existing one — it's asked for as a
first-class, daily-use surface ("Training + Nutrition + Bodyweight +
Progression as one system"), not a rarely-visited settings page (which
is why `apps.measurements`/`apps.activities`/`apps.records`, all real
but occasional-use, don't get their own slot). This is a real,
testable UI change (`BottomNavTests` gets updated alongside it), not a
casual addition.

## Testing strategy

Mirrors `apps/core/tests.py`'s `BMICalculationTests` (pure functions,
no DB) for `energy.py`/`macros.py`/`trends.py`, and
`apps/progression/tests.py`'s per-branch `TestCase` classes for the
suggestion engine — one class per calorie-adjustment scenario
(on-track, under-target, over-target, insufficient data, goal changed
mid-trend), plus a dedicated determinism test class the same way
progression has one. View/flow tests follow
`apps/measurements/tests.py`'s shape (permission/ownership 404s,
metric/imperial round-trips, HTMX flow tests). Explicit edge cases per
spec section 21: missing profile data, extreme weight/height/activity
inputs, goal reached, weight not moving, weight moving too fast, a
goal changed mid-history, a fat-loss goal flipped straight to a
muscle-gain goal.

## Phased implementation plan

Adapted from the spec's suggested order, reordered slightly so each
phase is independently testable and builds only on phases already
done:

1. **Domain model** — all models above, migrations, admin registration,
   seed migration for default `MealSlot`s.
2. **Energy engine** — `energy.py` (BMR/TDEE/goal→calories),
   `macros.py`, safety bounds. Fully unit-tested before any UI exists.
3. **Nutrition onboarding wizard** — collects `NutritionProfile` +
   first `NutritionGoal`/`NutritionTarget`, using the engine from #2.
4. **Food + food diary** — `Food` CRUD, `DiaryEntry` logging UI,
   day-total calories/macros display.
5. **Recipes** — `Recipe`/`RecipeIngredient`, logging a recipe into the
   diary.
6. **Weight-trend engine** — `trends.py`, `suggestions.py`, fully
   unit-tested against synthetic weight histories before any UI.
7. **Nutrition dashboard** — today's totals vs. target, weight trend,
   goal status, the adjustment-suggestion card from #6.
8. **Diet builder wizard** — `DietPlan` generation from
   Food/Recipe + targets, swap-one-item editing, "log today's plan."
9. **Training-day awareness** — the informational label described
   above, once the rest is live.
10. **Polish** — translations (6 languages, matching this project's
    existing catalog), API viewsets, `apps.api` docs, accessibility
    pass, full test suite + live verification, `docs/DEVELOPMENT_LOG.md`
    + `CHANGELOG.md` entries.
