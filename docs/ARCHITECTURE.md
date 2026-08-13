# Architecture

## Stack

- Django
- PostgreSQL
- Django Templates
- HTMX
- Alpine.js
- CSS
- Docker Compose

The application is server-rendered first.

## Application boundaries

Suggested Django applications:

```text
accounts
exercises
programs
workouts
records
progression
measurements
activities
analytics
core
```

`records` (PR engine) was not in CLAUDE.md's original suggested list; it
was added in Phase 5 as the structure evolved, per CLAUDE.md's "exact
structure may evolve if implementation demonstrates a better
organization." PRs are a big enough concern (own model, own detection
logic, own future dashboard) that folding them into `workouts` would have
started overloading that app; `records` depends one-directionally on
`workouts` (`PersonalRecord.source_set` and PR detection read
`ExerciseSet` history) and on `exercises` (`PersonalRecord.exercise`) —
neither of those apps knows `records` exists, so the dependency only ever
points one way.

Keep domain logic separated from presentation.

Views should orchestrate requests and responses. Complex rules belong in domain/service modules.

## Domain boundaries

### accounts
Authentication, profiles, preferences, units.

### exercises
Exercises, muscle groups, equipment, user-created exercises.

### programs
Programs, workouts, prescriptions, scheduling, templates, program versions.

### workouts
Workout sessions, performed sets, workout history.

### records
Personal record detection and storage (max weight, rep PRs, rep-specific
PRs, estimated 1RM, set/session volume). Derived from `workouts` history
only — never from `programs` — so program edits can't affect PRs.

### progression
Progression methods and weight suggestion logic. Has no models of its
own — a decision is recomputed live from `workouts` (session/set history)
and `records` (as one of the three 1RM sources for percentage-based
progression) each time, the same "derive, don't cache" approach `records`
uses for PRs. Not yet wired into any view/template — `docs/ROADMAP.md`
Phase 7 ("Smart suggestions") is where this becomes a UI-facing weight
suggestion; Phase 6 only had to deliver the underlying decision.

### measurements
Body weight, body fat, circumferences, custom measurements.

### activities
Manually logged non-gym activities.

### analytics
Aggregations, trends, dashboards, chart data. No models — every query is
computed live from `workouts`/`records` history, scoped by `apps.core.charts`'
model-agnostic chart builders and a small `dateranges` module
(`apps.analytics.dateranges`) shared by every date-range-filterable view.
`apps.measurements`/`apps.activities` already have their own dedicated,
working trend pages from Phases 8-9 — `analytics` doesn't duplicate
those, only what didn't have a home yet: cross-exercise training volume,
muscle-group volume, PR history, and per-exercise strength trend (1RM
over time).

### core
Shared utilities and cross-cutting concerns: unit conversion
(`apps.core.units`) and single-series chart data prep
(`apps.core.charts.build_chart_series` — normalizes a list of `(value,
date)` readings into SVG-ready coordinates; kept model-agnostic so any
app plotting a trend, currently `measurements` and `activities`, shares
one implementation instead of each rolling its own).

## Historical data rule

Completed workout data represents what actually happened.

A program edit must not rewrite historical sessions.

For example:

```text
Program v1
Bench Press: 3 × 8

Program v2
Bench Press: 4 × 8
```

A workout performed under v1 must remain a 3-set workout even after v2 exists.

Store enough snapshot/prescription information on the performed workout/set records to preserve historical truth.

### Historical integrity mechanism: snapshot-on-start

The mechanism chosen for this is **snapshot-on-start**, not full program
row-versioning.

When a `WorkoutSession` is created from a `Workout`, the prescription data it
needs (exercise, set count, rep range, target weight, progression method,
increment, ordering, notes) is copied onto the session's own performed
records at creation time. `Program`, `Workout`, and `ExercisePrescription`
rows can then be edited or deactivated freely afterward — history never
looks them up for its numbers, because it holds its own copy.

`Program` keeps a simple `updated_at` timestamp and an incrementing
`version` integer purely for display ("this program was edited on ...").
This is not a row-versioning system; there is no `ProgramVersion` table that
duplicates workouts/prescriptions. This keeps the schema small while fully
satisfying the historical-trustworthiness rule, since the snapshot — not the
live program — is the source of truth for anything already performed.

## Units and precision

- All weights, one-rep-max values, and body measurements are stored as
  `DecimalField` (not `float`), to avoid rounding errors compounding through
  progression math and PR comparisons.
- Canonical storage unit is kilograms for weight and meters for distance,
  regardless of the user's display preference. Conversion to the user's
  preferred unit system happens only at the template/service boundary.
- All timestamps are stored in UTC (`USE_TZ=True`); "today"/"this week"
  boundaries in the dashboard and analytics are computed using the user's
  stored timezone preference.

A post-launch audit found this rule wasn't actually followed everywhere:
`apps.measurements` converted correctly, but workout sets, PRs, exercise
prescriptions, and analytics totals/charts all stored and displayed raw
kilograms with a hardcoded "kg" label, and — more seriously —
`ExerciseSetForm`/`ExercisePrescriptionForm` stored whatever number was
*typed* as kg with no conversion at all, so an imperial-preference user's
entry was silently wrong by a factor of ~2.2. Fixed across the board: entry
forms convert to/from canonical kg the same way `BodyMeasurementForm`
already did; display goes through a shared `apps.core.units` dispatch
(`kg_to_display`/`display_to_kg`/`weight_unit_label`) — a `weight`
template filter for one-off spots (`apps.core.templatetags.core_extras`),
and `apps.records.services.format_value`/`format_previous_value` for PR
figures specifically, since a `rep_pr`'s value is a rep count rather than
a weight and must never be run through the conversion.

## Internationalization

UI text is translated with Django's own gettext-based `.po`/`.mo`
machinery — no extra dependency. Six languages ship: English (`en`,
also the source language every `msgid` is written in), Finnish (`fi`),
Swedish (`sv`), Russian (`ru`), Italian (`it`), and Estonian (`et`,
ISO 639-1 — not `ee`, which is Ewe).

- `User.language` (`apps.accounts`) is the per-user preference, set on
  the profile page — a distinct concern from `unit_system`/`timezone`
  above; UI language doesn't change what units or "today" mean.
  `apps.accounts.middleware.UserLanguageMiddleware` (after
  `AuthenticationMiddleware`, before the view) calls
  `translation.activate(user.language)` for a logged-in user, overriding
  `django.middleware.locale.LocaleMiddleware`'s own cookie/
  Accept-Language guess for that same request. There's deliberately no
  session/cookie caching of the choice on top of the database field —
  `user.language` is re-read every request, so there's only one place
  the value can ever live.
- Templates use `{% load i18n %}` + `{% trans %}`/`{% blocktrans %}`
  (with `{% blocktrans count %}` for plurals — gettext's real per-locale
  plural rules, e.g. Russian's three forms, not a naive `|pluralize`).
  Python-side strings (form labels/help_text/errors, model
  `verbose_name`, `TextChoices` labels, view flash messages, the
  progression engine's explanatory `reason` strings) use
  `gettext_lazy as _` at class/module-definition time and `gettext as _`
  inside view functions, matching Django's own convention for why the
  two differ (lazy vs. immediate evaluation).
- **What's translated vs. not**: all UI chrome — labels, buttons,
  messages, headings — is translated. Seeded reference *data* (exercise
  names, muscle groups, equipment, measurement/activity type names,
  built-in program template names/descriptions) is **not** — that's
  content translation, a different problem needing a
  model-translation layer (e.g. `django-modeltranslation`) rather than
  gettext, and would be a real new dependency without a strong enough
  reason yet per this project's "avoid unnecessary dependencies" rule.
  A user's own data (exercise names they typed, program names, notes)
  is never translated either way, correctly — gettext only ever matches
  strings that exist in the `.po` catalog.
- Workflow: `python manage.py makemessages -l <lang> ...` extracts
  every `{% trans %}`/`_()` call into `locale/<lang>/LC_MESSAGES/django.po`;
  `python manage.py compilemessages` builds the `.mo` files gettext
  actually reads at runtime. The latter runs automatically at container
  startup (`docker-compose.yml`/`docker-compose.override.yml`, right
  after `migrate`), so only the `.po` sources are committed — `.mo` is
  gitignored, the same "commit the source, generate the artifact"
  split as `static/` vs. `staticfiles/`.
- `gettext` (the GNU tool, providing `msgfmt`/`msguniq`/`msgmerge`) is a
  system package installed in the Docker image (`Dockerfile`) — needed
  for `compilemessages` to run at all, in both the dev and production
  images.
- Jargon abbreviations (RPE, RIR, 1RM, PR, "5RM"-style rep-max shorthand,
  BMI) are wrapped in an HTML `<abbr title="...">` wherever they appear
  — hovering (or a screen reader) reveals the full term without
  permanently lengthening the visible label. `apps.core.formatting`
  provides the canonical English expansion for each and two small lazy
  helpers: `abbr_label(abbreviation, expansion)` builds one HTML-safe,
  translatable `<abbr>` label (for a form field's `Meta.labels`, which
  must stay lazy — evaluated at class-definition time); `lazy_format_html`
  composes one together with surrounding plain text (e.g. "Target
  <abbr>RPE</abbr>") without forcing early evaluation. In templates,
  the same pattern is just inline `<abbr title="{% trans "..." %}">RPE</abbr>`
  — no helper needed there, since the template engine already defers
  rendering to request time.
- Regenerating the catalogs after adding new translatable strings: run
  `makemessages` for all six locales, then re-translate whatever
  `msgfmt --statistics locale/<lang>/LC_MESSAGES/django.po` reports as
  fuzzy or untranslated (`msgmerge`, which `makemessages` runs
  internally, fuzzy-matches a changed string against its closest former
  neighbor — useful as a starting point, but always wrong enough to need
  a human pass, and `compilemessages`/`msgfmt` silently skip
  fuzzy-flagged entries at compile time, falling back to the English
  source, so a fuzzy match left unreviewed isn't just imprecise, it's
  invisible).

## API layer

No REST/DRF API is built in the initial implementation. `ARCHITECTURE.md`'s
extensibility goal (future mobile clients, integrations) is satisfied by
keeping domain services HTTP-agnostic, which is already required. A thin API
layer can be added later without touching domain logic. Do not add Django
REST Framework or similar until an actual client needs it.

## Domain services

Important services should be independently testable.

Examples:

```text
ProgressionEngine
WeightSuggestionEngine
PRService
OneRepMaxCalculator
AnalyticsService
```

Avoid large monolithic services. Split responsibilities when they become difficult to test or understand.

`apps.records` implements `PRService` and `OneRepMaxCalculator` as plain
functions/a small class in `services.py`/`one_rep_max.py` rather than a
single `PRService` class — there was no shared state or polymorphism to
justify a class, per "keep the actual API clean and idiomatic" (see
`docs/PROGRESSION.md`, which asks the same of the future progression
service). `OneRepMaxCalculator` is a real class, though, since swapping
formulas (`docs/PR_SYSTEM.md`) is naturally an object with configuration.

## Security

Every user-owned object must be scoped to the authenticated user.

Explicitly test that one user cannot access another user's:
- programs
- workouts
- sets
- personal records
- measurements
- activities
- analytics data

## Extensibility

The architecture should allow future integrations such as health platforms or mobile applications, but these are not part of the initial implementation.
