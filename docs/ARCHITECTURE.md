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
  boundaries in the dashboard and analytics, and every rendered date/time,
  use the user's stored timezone preference —
  `apps.accounts.middleware.UserTimezoneMiddleware` calls
  `django.utils.timezone.activate(user.timezone)` once per request (same
  after-auth, before-view placement and "re-derive from the database
  every request" pattern as `UserLanguageMiddleware`), which both
  `timezone.localdate()`/`timezone.localtime()` calls and every plain
  `{{ some_datetime|date:"..." }}` template filter then read
  automatically. This is a real fix, not a restatement of what was
  already true: nothing ever called `timezone.activate()` before it, so
  every user saw UTC regardless of their profile setting, and "this
  week" itself could be computed wrong near a user's own midnight if it
  didn't line up with UTC's. `ProfileForm`'s timezone choices also drop
  a couple of non-geographic `zoneinfo` aliases ("localtime", "Factory")
  that are actively misleading rather than just unfamiliar —
  "localtime" reads as "detect my device's own timezone" but is a fixed
  server-side alias with nothing dynamic about it; see
  `apps.accounts.forms._MISLEADING_TIMEZONE_ALIASES`.

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
  built-in program template names/descriptions) **is** too, via the
  same gettext catalog rather than a model-translation library like
  `django-modeltranslation` — see "Seeded content" below for how. A
  user's own data (exercise names they typed, program names, notes,
  custom measurement/activity types) is never translated either way,
  correctly — gettext only ever matches strings that exist in the
  `.po` catalog.
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
- **Seeded content** (built-in exercise names, muscle groups, equipment,
  program template names/descriptions, workout names, measurement type
  names, activity type names — `apps.exercises`/`apps.programs`/
  `apps.measurements`/`apps.activities` migrations) *is* translated,
  unlike a user's own data, using the same gettext catalog rather than a
  model-translation library: the value **stored** in the database always
  stays canonical English (it's what `get_or_create(name=...)` and
  uniqueness constraints match against), but the **display** is looked
  up through the catalog at render time via `{% trans someobj.name %}`
  — Django's `trans` tag accepts a variable, not just a string literal,
  and runs its resolved value through `gettext()`. Since `makemessages`
  can only discover literal `_("...")`/`{% trans "..." %}` calls, not
  what a variable will resolve to at runtime, `apps.exercises.i18n_content`,
  `apps.programs.i18n_content`, `apps.measurements.i18n_content`, and
  `apps.activities.i18n_content` each hold a dedicated list of
  `gettext_lazy("...")` calls for every one of these seeded values —
  imported and executed by nothing, existing solely so `makemessages`
  extracts them into the catalog `{% trans someobj.name %}` then looks
  up. This is safe applied unconditionally, including on a user's own
  data (a custom exercise name, a personal program name): a string with
  no catalog entry is simply gettext's normal "no translation found"
  case, rendering exactly as typed rather than erroring or corrupting
  it. A `ModelChoiceField` (e.g. the exercise picker on a prescription
  form) needs one extra step beyond the template tag, since Django
  renders its `<option>` text via `str(obj)` internally, never reaching
  the template layer at all — `label_from_instance` is overridden
  (`apps.programs.forms.ExercisePrescriptionForm`,
  `apps.workouts.forms.PerformedExerciseAddForm`) to route that same
  text through `gettext()` too.
- **Gotcha: `{% trans someobj.name %}` breaks on a literal "%" in the
  content.** Django's `TranslateNode` (`django/templatetags/i18n.py`)
  doubles every `%` in a resolved *variable's* value before using it as
  the gettext msgid, then undoes the doubling on the way back out — a
  step meant for literal `%%` a template author writes by hand in
  template source to escape it from string-format interpolation, but
  applied unconditionally to variables too. `MeasurementType`'s seeded
  "Body fat %" hit exactly this: the tag looked up "Body fat %%", found
  no match, and silently fell back to the untranslated English string.
  `apps.core.templatetags.core_extras.translate_content` is a `|filter`
  that calls `gettext()` directly with no doubling, and is used instead
  of `{% trans %}` for `MeasurementType`/`ActivityType` names throughout
  `templates/measurements/`/`templates/activities/`. Exercise/program
  content currently contains no "%" so `{% trans someobj.name %}` is
  still correct there, but reach for `translate_content` instead for any
  future seeded content that might contain one.

## API layer

A real API (`apps.api`, Django REST Framework) was added on explicit
request — see `docs/API.md` for the full picture: authentication
(per-user API keys, `Authorization: Bearer`), authorization (per-key,
per-context CRUD permissions), and rate limiting (admin-editable tiers).
This section's original guidance ("no REST/DRF API... do not add DRF
until an actual client needs it") described the phase-1-through-11
state and is superseded for anything API-specific by `docs/API.md` — it
stayed true up to that point, and this repo's domain services having
stayed HTTP-agnostic all along (a requirement regardless of whether an
API existed) is exactly what made adding one later, without touching
any service function, actually straightforward rather than aspirational.
Every `apps.api` view calls the same `apps/*/services.py` functions the
server-rendered web views already call — see `docs/API.md`'s own
"Endpoints" section for concrete examples (set logging, PR detection,
ownership scoping) of what that means in practice.

## Admin site

Django's own admin (`/admin/`) is used as-is for staff/superuser
back-office tasks (seeded reference data, `RateLimitTier`/`ApiSettings`
tuning, user management) — re-themed to match IronStack's palette
(`static/css/admin_theme.css`, `templates/admin/base_site.html`,
branding set in `apps.core.admin`) rather than a hand-built parallel
admin page. Explicitly considered and rejected: a custom admin UI would
duplicate list views, filters, search, inline editing, and permission
checks the built-in admin already provides correctly, in direct
conflict with "do not create duplicate abstractions when an existing
one can be extended" — and every future model added to any app would
then need admin coverage written twice. The restyle only overrides
Django admin's own CSS custom properties (a supported, documented
customization point since Django 4.x/5.x, not template surgery), so
upgrading Django's admin internals doesn't require re-doing this work.
The admin is a desktop/power-user surface by design, distinct from the
mobile-first end-user UI everywhere else in this app — a legitimate,
common split for a self-hosted app's own back office, not an
inconsistency to fix.

## Versioning

A plain-text `VERSION` file at the repo root (e.g. `1.0.0`) is the
single source of truth for the running instance's version — not a
hardcoded Python constant and not derived from git at runtime. Two
reasons: it's baked into the Docker image the same `COPY . .` step
that bakes in the application code itself, so bumping a release is
one file edit plus a rebuild, no migration or settings change; and,
being plain text, it's trivially readable by tooling that isn't
Python at all — a future backup script can `cat VERSION` to stamp an
archive, and a future restore path can compare a backup's stamped
version against the running instance's before attempting to load it,
without either script needing to import Django. `apps.core.version.
get_version()` reads and caches it; `apps.core.context_processors.
app_version` puts it in every template's context (`{{ app_version }}`)
so any page can display it, even though only the profile page footer
does today.

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
