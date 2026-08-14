# IronStack

Self-hosted, mobile-first fitness and activity tracker. See `docs/` for the
full product/architecture/domain specification and `CLAUDE.md` for the
project's development guidelines.

## Status

Phase 1 (Foundation) — Django project scaffold, custom user model,
authentication, mobile-first base layout, Docker Compose stack.

Phase 2 (Exercises) — exercise library (`apps/exercises`): system-seeded
muscle groups, equipment, and a starter exercise set; user-created custom
exercises (private to their owner); browse/search/filter UI with HTMX-backed
live filtering; soft-delete via an `active` flag so history stays intact
after an exercise is retired.

Phase 3 (Programs) — programs (`apps/programs`): `Program` → `Workout` →
`ExercisePrescription`, private to their owner except built-in system
templates (seeded, read-only, copyable via `services.copy_program`, which
deep-copies workouts/prescriptions into a new program the user can edit
independently of the source). Optional per-workout weekday scheduling.
`Program.version`/`updated_at` are display-only counters, bumped on any
structural edit — see `docs/ARCHITECTURE.md` "snapshot-on-start" for why
this is enough to satisfy the historical-trustworthiness rule once workout
logging (Phase 4) exists.

Phase 4 (Workout logging) — workout sessions (`apps/workouts`):
`WorkoutSession` → `PerformedExercise` → `ExerciseSet`. Starting a session
from a `Workout` is where snapshot-on-start actually happens:
`services.start_session` copies each prescription's exercise, sets, rep
range, target weight, and progression method onto the session's own
`PerformedExercise` rows, so later edits or deletes of the prescription
never change what the session already recorded (covered directly by
tests). Sessions can also start freeform (no program) and exercises can be
added mid-session beyond what was planned. Set logging is HTMX-driven —
each submit swaps in the updated set list and a fresh entry form
pre-filled by repeating the last set's weight/reps, so back-to-back sets
need no retyping; falls back to a normal page redirect without
JavaScript. Sessions, performed exercises, and sets are strictly private —
never shared like programs' system templates. Workout history lists
in-progress, completed, and abandoned sessions alike.

Phase 5 (PR engine) — a new `apps/records` app (not in the original
suggested list; see `docs/ARCHITECTURE.md` for why). `PersonalRecord` is
an append-only achievement log covering all six PR types from
`docs/PR_SYSTEM.md` (max weight, rep PR, rep-specific PR/"NRM", estimated
1RM, set volume, session volume). Detection (`services.check_and_record_prs`)
runs once per newly logged set, always comparing against live-computed
history — nothing is cached from a prior run, and the module never
references `Program`/`Workout`/`ExercisePrescription` at all, so program
edits provably can't touch PRs. Warmup and failed sets never count.
Estimated 1RM goes through a swappable `OneRepMaxCalculator`
(`one_rep_max.py`, Epley by default). Session volume is the one type
that's a running total rather than a single set's raw number, so later
sets in the same session update the existing record instead of each
firing their own notification — otherwise a good session would spam a
"New PR" banner after every set. Set logging shows a PR banner
(HTMX-instant, plus a Django message as a no-JS fallback); each exercise
also has a "Your PRs" page showing current bests computed live. 28 new
tests (92 total).

Phase 6 (Progression engine) — `apps/progression/engine.py`:
`calculate_progression(user, prescription)` implements all seven methods
from `docs/PROGRESSION.md` (manual, maintenance, linear, double
progression, rep range, percentage-based, RPE/RIR) as pure domain logic —
no models, no views yet (that's Phase 7, "Smart suggestions"; this phase
only had to get the decision right). Every method judges past sessions
against what was actually snapshotted for them at the time (not the live
prescription), consistent with the historical-trustworthiness rule.
Failure handling is uniform: one missed session maintains, two
consecutive missed sessions at the same weight escalates to a 10%
deload. Percentage-based tries three 1RM sources in priority order —
manually supplied, latest estimated-1RM PR (`apps/records`), or a live
estimate from the most recent set — always reporting which one it used.
26 new tests (118 total), including a determinism check (same inputs →
identical `ProgressionResult`) and per-user isolation.

Phase 7 (Smart suggestions) — `apps/progression/suggestions.py`:
`WeightSuggestionEngine` (`suggest_weight(user, prescription)`), kept as
its own module separate from `ProgressionEngine` per
`docs/SMART_SUGGESTIONS.md`'s architecture note. Composes the Phase 6
decision with the prescription's rep range and a deterministic
low/medium/high confidence (based on how many sessions of evidence
backed the decision, or which 1RM source percentage-based used — never a
black-box score). Reaches the logging UI via
`apps.workouts.views._build_set_form`: a performed exercise's first set
gets pre-filled from the suggestion, shown alongside its confidence and
plain-language reason — purely a form default the user can freely
override before submitting, same as the existing "repeat last set"
convenience it falls back to for every set after the first. Since
`apps.progression` already depends on `apps.workouts` (it reads session
history), the composition happens at the view layer rather than in
`apps.workouts.services`, to avoid a circular app dependency. 14 new
tests (132 total), including the doc's own worked "reached the top of the
rep range in the last two sessions → 82.5 kg" example, and an end-to-end
test logging a materially different weight than the one suggested to
confirm nothing blocks overriding it. Also caught and fixed a real
rendering bug during manual verification: Python's `str, Enum` mix
stringifies as `Confidence.HIGH` instead of `high` — switched both
`Confidence` and `ProgressionAction` to `enum.StrEnum`.

Phase 8 (Body tracking) — `apps/measurements`: `MeasurementType`
(system-seeded weight/body fat %/waist/chest/arm/thigh/hip/neck, plus
user-created custom types — same ownership/soft-delete pattern as
`apps.exercises.Exercise`) and `BodyMeasurement` (a time-stamped
reading). `apps.core.units` gained cm/inch conversions (circumferences
round-trip through the meters canonical unit at 0.1mm precision so no
realistic input gets rounded away); `apps.measurements.units` dispatches
by `unit_kind` plus the user's metric/imperial preference onto those, so
a value is entered and displayed in whatever unit the user actually
reads, converted to canonical storage on save. Each type gets its own
history page: a hand-rolled inline-SVG line chart (no new JS dependency —
plotted server-side via `services.build_chart_series`, keeping the
scaling/normalization math out of the template) alongside the full
editable table, which doubles as the chart's accessible data source. 27
new tests (161 total). Manual HTTP verification caught and fixed a real
display bug the test suite's exact-value assertions didn't: the chart
used raw 4-decimal-place canonical values instead of the user's converted
display units — now both the chart and table read from the same
converted values.

Phase 9 (Activities) — `apps/activities`: `ActivityType` (a starting set
of common types seeded — Running, Walking, Cycling, Swimming, Hiking,
Rowing, Yoga, Other — not an exhaustive doc-specified list like
`MeasurementType`, since `docs/DOMAIN_MODEL.md` expects users to add
their own) and `Activity` (date, optional start time, duration, optional
distance/calories, notes). `build_chart_series` was generic enough
already that this phase needed the exact same thing Phase 8 built for
measurements, so rather than duplicate it or have `apps.activities`
import from the unrelated sibling `apps.measurements`, it was promoted to
`apps.core.charts` — both apps' history pages, and the shared chart
template (`templates/core/_chart.html`), now use the one shared
implementation, model-agnostic via plain `(value, date)` tuples. Each
activity type's history page charts total duration (the one metric every
activity type has, unlike distance/calories which are optional) alongside
a summary card (count, total duration, total distance/calories) and the
full editable table — this phase's "activity analytics"; cross-activity
dashboards are Phase 10. 25 new tests (186 total) plus a chart-test
relocation to `apps.core` to match the refactor. Manual HTTP verification
again caught a real bug past the test suite: the summary card's total
distance displayed raw canonical meters labeled "km" (e.g. "18000.00 km"
for what should read "18.00 km") — `services.summarize()` correctly
totals canonical values, but the view wasn't converting the total before
handing it to the template, unlike the already-converted per-entry
values. Fixed and re-verified live, with a regression test added.

Phase 10 (Analytics) — `apps/analytics`. `apps.measurements`/
`apps.activities` already had dedicated trend pages from Phases 8-9, so
this phase adds only what didn't have a home yet: a dashboard
(`/analytics/` — training summary, a weekly-volume bar chart, a
muscle-group-volume bar chart, PR history) and per-exercise strength
trend (`/analytics/exercises/<pk>/` — estimated 1RM over time, one point
per session). `apps.core.charts` gained `build_bar_series` alongside the
existing line-chart builder, so both chart types share one tested,
model-agnostic foundation. Date-range filtering
(`apps.analytics.dateranges`) is shared across both pages: six presets
plus an explicit `start`/`end` override for docs/ANALYTICS.md's "custom
range". Training-load volume here deliberately counts failed sets (the
work still happened) — a real, intentional divergence from
`apps.records`' stricter PR eligibility, not an inconsistency. Also
enhanced `apps.core`'s main dashboard with the widgets
`docs/UI.md` calls for: this week's volume, recent PRs, latest body
weight. 51 new tests (222 total). Manually seeding realistic workout
history to verify live surfaced a false alarm rather than a real bug —
"Training time: 32 days" from a first pass — traced to the seeding
script itself (it backdated `started_at` without also backdating
`ended_at`), not the analytics query; re-seeding with a realistic
`ended_at` showed the correct duration, confirming the calculation was
right all along. Also confirmed the estimated-1RM trend, both bar
charts, and the date-range filter each read correctly against real
multi-session history over HTTP.

Phase 11 (Polish) — final pass across the whole app rather than one app's
worth of new features:

- **Query review**: two real N+1s found and fixed. The programs list
  called `program.workouts.count` per row in the template (twice —
  once for the number, once for `|pluralize`); the activities list
  called the full `services.summarize()` per row, materializing every
  logged `Activity` row just to display a count. Both replaced with a
  single annotated `Count()` query per list — the activities fix
  specifically filters on the current user (`Count("activities",
  filter=Q(activities__user=user))`), since an unfiltered count would
  have shown every user's total against a shared system type. Added
  `db_index=True` to the four date fields every history/analytics view
  filters and orders by (`WorkoutSession.started_at`,
  `PersonalRecord.achieved_at`, `BodyMeasurement.recorded_at`,
  `Activity.date`) — targeted at query patterns already built, not
  speculative.
- **Accessibility**: a skip-to-content link, `.htmx-request` styling so
  every HTMX interaction in the app gets free loading feedback with no
  per-form wiring, and an audit confirming every hand-rolled `<input>`/
  `<select>` outside the standard `field.label_tag` form loop already
  carries an explicit `aria-label`.
- **Error handling**: custom 404/403 templates (extend `base.html` —
  Django passes them a normal request context) and a deliberately
  *standalone* 500 template with hand-copied inline styles, since Django
  renders it with no context processors at all — the moment it's needed
  may mean the DB or session backend itself is down, so it can't lean on
  `{% url %}`, `{% static %}`, or anything else `base.html` depends on.
- **Mobile**: wide tables (set logging, measurement/activity history)
  now scroll within themselves (`.table-wrap { overflow-x: auto }`)
  instead of pushing the whole page wider on narrow screens.
- **A real functional gap, not just visual polish**: the "Profile"
  bottom-nav tab had been a dead `href="#"` placeholder since Phase 1 —
  and `unit_system`/`timezone` had driven unit conversion since Phase 8
  with no UI to ever change them after signup. Built
  `apps.accounts.ProfileView` (unit system, timezone — a real
  `zoneinfo.available_timezones()` dropdown, not free text) and wired up
  Django's built-in password-change flow, whose URLs had existed since
  Phase 1 but had no templates — visiting `/accounts/password_change/`
  would have thrown `TemplateDoesNotExist`.
- **Docker production review**: `python manage.py check --deploy`
  against `config.settings.production` passes clean. Found and fixed a
  real deployment footgun: the bundled nginx config is HTTP-only, but
  `DJANGO_SECURE_SSL_REDIRECT` defaults `true` — deployed exactly as
  shipped, this is an infinite redirect loop (nginx always reports
  `X-Forwarded-Proto: http`, Django redirects every request to HTTPS,
  which routes right back through the same HTTP-only nginx). Documented
  clearly in `docs/SECURITY.md` with the three ways to actually resolve
  it, rather than silently shipping a broken default or bundling
  certificate automation that can't be generically tested without a real
  domain. Also added a `web` healthcheck (hits the existing `/healthz/`)
  and gated nginx's startup on it (`depends_on: web: condition:
  service_healthy`) instead of nginx merely waiting for the container to
  start rather than actually be ready — verified live: nginx now visibly
  waits through "Waiting → Healthy" before starting. Added explicit
  gunicorn worker/timeout/logging flags, previously left at gunicorn's
  bare defaults (1 worker).
- 10 new tests (232 total): profile updates (including an invalid-
  timezone rejection), the password-change flow end-to-end (change
  password, confirm the new one authenticates), both N+1 fixes'
  annotated counts (including the cross-user scoping correctness for the
  activities one), and the custom error templates.

Post-v1 fixes and additions (user-requested, not a numbered phase):

- **Chart titles/legends audit** — every chart's title used to reach only
  the SVG `aria-label` (invisible to sighted users) on three pages; the
  bar charts had no visible category labels at all despite a code
  comment claiming otherwise. Both fixed — see `docs/ANALYTICS.md`
  "Chart titles/legends audit".
- **Desktop nav** — was briefly, genuinely broken ("way too big"): CSS
  Grid's default stretch + an inherited mobile `flex: 1` combined to make
  each of the 5 sidebar links grow into an equal fifth of the full page
  height. Rewritten as a horizontal top bar (matching mobile's row
  direction instead of switching to a column), which fixes the bug more
  fundamentally than patching the sidebar would have.
- **Mobile nav** — reordered to Home, Progress, Workout, Programs,
  Profile; icon-only (hand-drawn inline SVGs) with the label visually
  hidden and carried instead by each link's `aria-label`, reappearing as
  text on desktop where there's room for both.
- **Training-time duration formatting** — a raw `{{ timedelta }}` was
  rendering real seconds/microseconds ("0:03:19.893476"). Added a shared
  `duration` template filter (`apps.core.templatetags.core_extras`,
  rounds to the nearest minute) used everywhere a training/activity
  duration renders.
- **Activity date/time inputs** — now native `type="date"`/`type="time"`
  pickers instead of plain text, with explicit widget `format=` so
  editing an existing entry pre-fills correctly (Django's
  locale-dependent default format doesn't reliably match what those
  input types expect — same pitfall as this app's earlier
  datetime-local decision).
- **Direct workout-delete button** — `workout_delete` already worked but
  was only reachable via Edit workout → Delete workout; now also a
  direct button on the program page next to each workout's Edit link.
- **Installable PWA** (explicit request; `docs/ROADMAP.md`'s "Future
  possibilities" specifically says not to build this unprompted, but an
  explicit ask overrides that caution) — a web manifest, a hand-drawn
  barbell icon, and a minimal service worker, served at the site root
  (`/manifest.json`, `/sw.js` — not `/static/`, which matters for the
  service worker's scope). Deliberately installable only, not
  offline-capable: the worker only cache-first's genuinely static assets
  and never intercepts a page, form, or HTMX response, so nothing here
  can show stale workout data or silently lose a logged set while
  offline — see `docs/ROADMAP.md`'s "Future possibilities" for exactly
  where that line is drawn.
- Investigated a reported "date range doesn't filter analytics" bug and
  could not reproduce it — seeded sessions 2 and 60 days apart and
  confirmed over HTTP that every stat and the weekly-volume chart's own
  bar count correctly change across every preset. Left as-is; most
  likely explanation is the account being tested only had data within a
  short recent window.
- 23 new tests (255 total).

**Personal program templates, and three more well-known built-in
templates.** `Program.is_template` already existed (Phase 3, system
templates only) but was never exposed for a user's own programs.
`ProgramForm` now includes it ("Save as a personal template"); the
program page shows a "My template" tag and a "Copy to a new program"
button for any of the user's own template-flagged programs, the same
copy mechanism system templates already used — copying only ever depends
on being able to *see* the source program at all, never on `is_template`
itself, so a personal template stays exactly as private as any other
program (tested explicitly: another user still gets a 404 trying to copy
someone else's template). Seeded three more built-in templates alongside
the original generic "Full Body A/B/C": Arnold Split (the classic 6-day
bodybuilding split), Push/Pull/Legs, and 5×5 Strength — real,
widely-documented training methodologies; naming one after the lifter
who popularized it is standard practice in fitness literature and
implies no endorsement. All four system templates verified copyable
end-to-end in a single test. 9 new tests (264 total).

**Date/time pickers, workout deletion, "Back to" buttons, mobile nav
sizing, unit-consistency audit, and BMI.**

- **Remaining date/time field** — `BodyMeasurementForm.recorded_at` was
  the one field the earlier date/time-picker pass missed (a plain-text
  datetime), now a native `datetime-local` picker with the same
  widget-`format=` + field-`input_formats=` combination the activity
  date/time fix already established (Django's default
  `DATETIME_INPUT_FORMATS` never includes the "T"-separated format
  `datetime-local` submits).
- **Delete a logged workout** — a `session_delete` view, reachable from
  the workout's own detail page (with a confirm dialog) but deliberately
  *not* from the `/workouts` history list, which can show many sessions
  at once — keeping a destructive action out of reach of an accidental
  tap on a list row.
- **"Back to X" links restyled as buttons** — every one of them (program,
  exercise, workout, measurement, activity, records, and form pages, plus
  the error pages) was a bare text link, easy to miss and a small touch
  target. New `.button-secondary` style (outlined, lower-emphasis than
  the solid `.button` used for primary actions).
- **Mobile nav enlarged** — icons grown from 1.5rem to 1.9rem and the bar
  from 3.5rem to 4.25rem tall, plus explicit top padding on each link so
  icons aren't flush against the bar's top edge; motivated specifically
  by how cramped the previous sizing felt in a PWA's standalone window
  (no browser chrome to lean on for scale reference).
- **Unit-consistency audit (metric/imperial)** — a real, significant bug:
  outside `apps.measurements` (which already converted correctly),
  *every* weight in the app — workout sets, PRs, exercise prescriptions,
  analytics totals/charts, the "New PR" flash message — was stored and
  redisplayed as raw kilograms with a hardcoded "kg" label, regardless of
  the user's unit preference. Worse, `ExerciseSetForm.weight` and
  `ExercisePrescriptionForm.target_weight`/`weight_increment` took
  whatever number was typed and stored it *as kg* with no conversion —
  an imperial user entering "225" got 225 kg stored, not 225 lb converted
  to ~102 kg. Fixed at both ends: entry forms now convert to/from
  canonical kg the same way `BodyMeasurementForm` already did, and
  display (analytics service functions, chart series, PR figures, the
  flash message) converts via a shared `apps.core.units` dispatch — a new
  `weight` template filter for one-off spots, and
  `apps.records.services.format_value`/`format_previous_value` for
  record figures specifically (record-type-aware: a `rep_pr`'s value is
  a rep count, not a weight, so it's never run through unit conversion).
- **Height + BMI** — `User.height` (canonical meters, entered/displayed
  in cm or inches like any other length reading) plus `apps.core.bmi`
  (WHO category thresholds — underweight/normal/overweight/obese). The
  dashboard shows the current BMI and category alongside the full ranges
  table (with the user's own category row highlighted) whenever both a
  height and a logged body weight exist; a `show_bmi` profile toggle
  turns the whole feature off outright for anyone who'd rather not see
  the number, independent of whether it's computable.
- **/workouts list delete removed, dashboard logout button removed**
  (follow-up requests) — deleting stays a detail-page-only action; log
  out stays reachable from the profile page only, not duplicated on the
  dashboard.
- **Nav vs. dashboard duplication** — the "Progress" nav tab linked to
  Body tracking (measurements), not the actual analytics/progress-charts
  page, and the dashboard carried its own "Analytics", "Workout
  history", and "Programs" cards that led to exactly the same places the
  main nav already did. "Progress" now points to the analytics
  dashboard; the three redundant cards are gone (Body tracking, Browse
  exercises, and Activities stay as dashboard cards — none of the three
  has its own nav slot).
- **BMI toggle styling** — the checkbox rendered through the same
  block-label-then-field layout as every text field, stacking "Show BMI
  on the dashboard" above an isolated checkbox instead of the two
  sitting together. New `.checkbox-field` layout (box beside its own
  label) for any boolean field going forward. Caught in the same pass: a
  multi-line `{# ... #}` Django comment in that template rendered
  literally instead of being stripped — Django's single-line comment tag
  doesn't reliably span lines; multi-line explanatory comments need
  `{% comment %}...{% endcomment %}` instead.
- **Top-bar/card spacing** — `.top-bar` had no `margin-bottom`, so its
  bottom border sat flush against whatever followed; on any page where a
  `.card` came immediately after, the two borders touched with no gap,
  reading as one doubled/overlapping line rather than a header
  underline. Fixed with a `margin-bottom` matching `.card`'s own spacing
  rhythm.
- **Overlapping/oversized buttons on the program page** — `.button`
  (used on `<a>` tags for "Edit"/"Add exercise"/"Add workout") had no
  `display` set, so it defaulted to plain inline — an inline box's
  padding and `min-height` don't reserve real space in normal document
  flow, so the button visually overlapped whatever block-level element
  (typically a sibling `<form>`/`<button>`) immediately followed it. Now
  `display: inline-flex` (properly sized *and* centers its label) with a
  `margin-bottom` for breathing room between stacked action rows;
  `.set-actions button`'s existing small-button override keeps that
  layout (workout Edit/Delete, set-row Edit/Delete) unaffected.
- 34 new tests (298 total).

**Internationalization (i18n) — six languages.** All UI text (templates,
form labels/help_text/errors, model choice labels, flash messages, the
progression engine's explanatory `reason` strings) is now marked for
translation and translated into English, Finnish, Swedish, Russian,
Italian, and Estonian, using Django's own gettext `.po`/`.mo`
machinery — no new dependency beyond the `gettext` system package
(`Dockerfile`) `compilemessages` needs to build `.mo` files from the
committed `.po` sources, run automatically at container startup. A new
`User.language` field (profile page, alongside unit/timezone
preferences) drives it via `apps.accounts.middleware.UserLanguageMiddleware`,
which re-derives the active language from the database on every request
rather than caching it in a session or cookie. Deliberately out of
scope: seeded reference data (exercise names, muscle groups, built-in
program templates, ...) and any user-entered text — those are content,
not UI chrome, and would need a model-translation layer
(`django-modeltranslation` or similar) to translate at all; gettext only
ever matches strings actually present in its `.po` catalog, so a
user's own data always renders exactly as typed, in every language.
Caught and fixed two real bugs along the way: Django's own
`LANGUAGE_SESSION_KEY` doesn't exist in this Django version (the
middleware doesn't need a session at all, so it was simplified rather
than worked around), and Russian's PLURAL_FORMS needs its real 3-way
rule (one/few/many — e.g. "3 тренировки" vs. "5 тренировок"), not
gettext's English-shaped 2-form default. See `docs/ARCHITECTURE.md`
"Internationalization" for the full write-up. 5 new tests (310 total).

**Bar chart labels, and two button placement fixes.** Bar charts (weekly
training volume, muscle-group volume) had deliberately unlabeled bars —
a color-coded row with no visible name, readable only via the table
below or a hover tooltip — which read as broken rather than just
minimal. Each bar now carries its own rotated `<text>` label directly in
the SVG (`apps.core.charts.build_bar_series` now reserves a label band
and computes each bar's label position); the exact figures still live in
the table below. Also added explicit `width`/`height` attributes to
every chart `<svg>` (previously `viewBox`-only) — a defensive fix for
inconsistent height:auto intrinsic-sizing behavior across browsers when
only `viewBox` is present. "Start freeform workout"
(`templates/workouts/session_list.html`) and "New"
(`templates/programs/program_list.html`) were both sitting crammed
against their page heading in the top-bar; moved out to their own
better-separated spots — freeform-start now sits as a secondary-styled
button beside "Back to programs" at the bottom, "New" sits directly
under the "My programs" heading, above that list. 1 new test (311
total).

**Abbreviation tooltips, and BMI category ranges as actual weight.**
RPE, RIR, 1RM, PR, the "5RM"-style rep-max shorthand, and BMI are all
real jargon to a new user — every occurrence (form field labels
included) now wraps the abbreviation in an HTML `<abbr title="...">`,
so hovering (or a screen reader) reveals "Rate of Perceived Exertion",
"Reps In Reserve", "One-Rep Max", "Personal Record"/"Personal Records",
"Rep Max", and "Body Mass Index" respectively, without permanently
lengthening the visible label. New `apps.core.formatting` module holds
the canonical expansion text plus two small lazy helpers
(`abbr_label`, `lazy_format_html`) for building a translatable,
HTML-safe form-field label at class-definition time — genuinely
necessary because a Django `Meta.labels` value has to stay lazy
(`gettext_lazy`), and a lazy proxy only renders unescaped HTML if its
resolved type is itself already `SafeString`. Separately, the BMI
category ranges table (`apps.core.bmi.category_rows`) now also shows
the equivalent weight range for each category once a height is on
file — "Normal weight" as a bare "18.5–25" BMI-number range doesn't
say much on its own; "59.9–81.0 kg" does. All 320 extracted strings
were retranslated in this pass too — several existing strings had to
split around their new `<abbr>` tags (e.g. "Estimated 1RM:" → "Estimated"
+ the tag), which produces a fresh, untranslated `msgid` even though
the sentence itself didn't really change; `msgmerge`'s fuzzy-match
against the old string is a reasonable starting point but reliably
wrong (and `compilemessages` silently skips fuzzy entries, falling
back to English), so every fuzzy/untranslated entry got a deliberate,
reviewed translation instead. 5 new tests (319 total).

**Mobile-friendly `<abbr>` tooltips, 10 more exercises + 2 more program
templates, and translating all seeded exercise/program content.**

- The `<abbr title="...">` tooltips added in the previous pass only
  ever reached a mouse user — iOS/Android give no gesture that reveals
  a plain `title` attribute, so on a touchscreen (this is a
  mobile-first app) they were effectively invisible. Every `<abbr>`
  (`apps.core.formatting.abbr_label`, and the hand-written ones in
  templates) now carries `tabindex="0"`, and `static/css/base.css` adds
  a small themed tooltip shown on `:focus` as well as `:hover` —
  content attribute-derived (`content: attr(title)`), so tapping,
  keyboard-focusing, or hovering the abbreviation all reveal the same
  expansion now.
- 10 more built-in exercises (`apps.exercises` migration 0004: Romanian
  Deadlift, Front Squat, Incline Barbell Bench Press, Hip Thrust, Seated
  Cable Row, Face Pull, Lateral Raise, Hammer Curl, Skull Crusher, Ab
  Wheel Rollout — 25 total now) and 2 more program templates
  (`apps.programs` migration 0006: Upper/Lower Split (4-Day), German
  Volume Training — 6 total now), using only muscle groups/equipment
  already seeded.
- Built-in exercise names, muscle groups, equipment, and program
  template names/descriptions/workout names are now translated for
  display in all 6 languages — genuinely different from the UI-chrome
  translation earlier: this is database *content*, so the value
  **stored** in the DB always stays canonical English (still what
  `get_or_create(name=...)`/uniqueness constraints match against), and
  only the **display** goes through gettext, via `{% trans someobj.name %}`
  (Django's `trans` tag accepts a variable, running its resolved value
  through `gettext()` — not just a string literal). Since `makemessages`
  can't discover what a variable will resolve to at runtime, two new
  extraction-only modules (`apps.exercises.i18n_content`,
  `apps.programs.i18n_content` — imported and executed by nothing) each
  hold a `gettext_lazy("...")` call per seeded name/description so
  `makemessages` finds them anyway. This is safe to apply
  unconditionally, including to a user's own data (their custom
  exercise names, their own program names) — a string with no catalog
  entry is just gettext's ordinary "no translation" case, rendering
  exactly as typed rather than erroring. One extra step for the
  exercise-picker `<select>` (`ExercisePrescriptionForm`,
  `PerformedExerciseAddForm`): Django renders a `ModelChoiceField`'s
  `<option>` text via `str(obj)` internally, bypassing the template
  layer entirely, so `label_from_instance` is overridden to route that
  through `gettext()` too. No new dependency (no model-translation
  library) — this is the same gettext catalog the UI-chrome pass
  already built, just fed from two more source locations. All 397
  extracted strings (up from 320) translated across all 5 non-English
  catalogs; `msgfmt --statistics` confirms 0 fuzzy/untranslated. 9 new
  tests (324 total).

**Translate seeded measurement/activity type names too — the one
remaining content-translation gap.**

- Auditing "is anything still unimplemented?" turned up a genuine, real
  gap: the previous pass translated exercise/muscle-group/equipment/
  program content but never gave the same treatment to `MeasurementType`
  (Body weight, Body fat %, Waist, Chest, Arm, Thigh, Hip, Neck) or
  `ActivityType` (Running, Cycling, Swimming, Walking, Hiking, Rowing,
  Yoga, Other) names — seeded content structurally identical to the
  exercises/programs case. Fixed the same way: two new extraction-only
  catalog modules (`apps.measurements.i18n_content`,
  `apps.activities.i18n_content`), templates wrapping the display value
  instead of the stored one.
- This surfaced a genuine Django limitation, not just an oversight:
  `{% trans someobj.name %}` doubles every literal `%` in a resolved
  *variable's* value before the gettext lookup and undoes the doubling
  afterwards (`TranslateNode`'s "restore percent signs" step — meant for
  a `%%` a template author writes by hand in template source, applied
  unconditionally to variables too). `MeasurementType`'s seeded "Body
  fat %" hit this exactly: it looked up "Body fat %%", found nothing,
  and silently rendered the untranslated English string instead of
  erroring — the kind of bug that only shows up by actually reading the
  rendered page in the target language, not by reasoning about the code.
  Fixed with a new `translate_content` filter
  (`apps.core.templatetags.core_extras`) that calls `gettext()` directly
  with no doubling; used in place of `{% trans %}` throughout
  `templates/measurements/`/`templates/activities/`. Exercise/program
  content has no `%` today so `{% trans someobj.name %}` stays correct
  there, but `translate_content` is now the documented answer for any
  future seeded content that might contain one — see
  `docs/ARCHITECTURE.md` "Internationalization".
- 16 new strings extracted and translated across all 5 non-English
  catalogs (411 total, up from 397); `msgfmt --statistics` confirms 0
  fuzzy/untranslated. 9 new tests (333 total).

**Training mode** — a focused, one-page, in-workout screen: current
exercise, what's next, a rest timer, and the smart weight suggestion,
reachable from a floating button on every page while a session is in
progress.

- A new floating action button (`.training-fab`, dumbbell icon, small
  pulsing "live" dot) appears bottom-right on *every* page — not just
  the workout ones — whenever the logged-in user has a session in
  progress, via a new global context processor
  (`apps.workouts.context_processors.active_workout_session`). Tapping
  it opens `/workouts/<id>/train/`.
- Training mode shows one exercise at a time: target, the same
  `suggest_weight` suggestion the full session-detail page shows (still
  just an editable default, never forced), sets already logged, and a
  compact log-set form (weight/reps up front, RPE/RIR/notes/warmup/
  failure behind a "More options" disclosure). The "current" exercise is
  automatic — the first one still short of its target set count
  (`apps.workouts.services.is_performed_exercise_complete`/
  `first_incomplete_performed_exercise`) — but Prev/Next always lets a
  user jump anywhere regardless, matching "the user always has final
  control" (CLAUDE.md). Logging an exercise's last set auto-advances to
  the next incomplete one.
- A pure client-side (Alpine.js) rest timer with 60/90/120s presets,
  ±15s adjust, and skip — no model field, no server round trip while
  it's running. Auto-starts after a successful log via htmx's
  `HX-Trigger` response header, which is what lets the view tell
  "successfully logged" apart from "validation error, same 200 status,
  re-showing the form" — a generic `htmx:afterRequest` listener can't
  make that distinction. The timer widget lives outside the HTMX swap
  target on purpose, so a countdown in progress survives every panel
  swap.
- Fully progressive-enhancement-safe: every interaction is a real link/
  form POST first, HTMX just upgrades it to a partial swap.
  `train_set_log` explicitly checks for `HX-Request` and redirects to
  the full page otherwise, rather than ever returning the bare
  training-panel fragment (no `<head>`/stylesheet/nav) as if it were a
  whole document.
- Two real, pre-existing bugs turned up while building and testing this
  and got fixed alongside it, unrelated to training mode itself but
  found because of it: (1) `templates/500.html`'s own comment
  explained, using literal Django tag syntax, that the page "can't rely
  on the url tag" — but Django's template lexer parses that syntax
  anywhere in the file regardless of surrounding CSS/HTML comments, so
  that comment was itself a broken, argument-less tag invocation. The
  custom error page crashed on every real 500, meaning a genuine server
  error would have shown Django's raw default page instead. (2) None of
  `apps.workouts`' function-based views (only its class-based ones) ever
  required login — `services.sessions_for(AnonymousUser())` crashes
  rather than returning empty, so an anonymous visitor hitting any of
  them 500'd instead of getting a clean redirect to login. Decorated the
  two new training-mode views (`session_train`, `train_set_log`) with
  `@login_required`; the older sibling views share the same latent gap
  but are unchanged here — out of scope for this feature.
- `docs/UI.md` gained a full "Training mode" implementation section. 10
  new UI strings translated across all 5 non-English catalogs (421
  total). 26 new tests (358 total).

**New-PR notifications became top-of-screen toasts.**

- Every "New PR" notice — from the full session-detail page and training
  mode alike — now renders as a toast fixed to the top of the screen
  (`#pr-toast-container`, defined once in `base.html`) instead of an
  inline banner buried inside whichever exercise card triggered it.
  Implemented as an HTMX out-of-band swap
  (`templates/records/_pr_toasts.html`, shared by both card templates)
  rather than any new JS state — the same rich per-PR detail (max
  weight/rep PR/1RM/set & session volume, previous value) that used to
  live in `.pr-banner` renders exactly as before, just relocated. Each
  toast auto-dismisses after 6 seconds (Alpine `x-show` + a timeout) and
  carries its own close button, so timing stays overridable —
  CLAUDE.md/`docs/UI.md`'s "user always has final control" applies to a
  toast's lifetime too, not just to weights and progression.
- Fixed a real, related bug found while doing this: `messages.success`
  for a new PR used to fire unconditionally, including on every HTMX
  request — but nothing ever consumes `django.contrib.messages` there
  (only `base.html`'s full-page `{% if messages %}` loop does), so the
  message sat in the store and would resurface, stale, on whatever
  unrelated full page the user happened to load next. Now only the no-JS
  fallback path (a plain POST + redirect) flashes a message; the HTMX
  path gets the toast instead.
- 1 new UI string (`"Dismiss"`, the toast close button's label)
  translated across all 5 non-English catalogs (422 total,
  0 fuzzy/untranslated). 4 new tests (362 total).

**Achievements carousel (shared across all users), a rest-timer sound +
mute toggle, and two mobile layout fixes.**

- A new dashboard carousel (`apps.analytics.achievements`) auto-rotates
  through all-time highlight cards — longest streak (consecutive
  calendar days with a completed workout), total workouts, total PRs,
  total weight lifted — pausing on hover/focus, always overridable via
  a dot row. Unlike every other dashboard widget it's **shared across
  every user on the instance**: each card names whose achievement it
  is, and a new `User.show_achievements` field is a *privacy* setting
  (default on) rather than a personal display toggle — turning it off
  removes that user's own figures from what the carousel shows to
  everyone, themselves included, not a "hide the carousel from me"
  switch. Requested, then refined mid-build once the first (personal-
  only) version was up: the carousel became shared, the toggle's
  semantics flipped to match, and usernames were added to each card.
- Fixed a real layout bug found immediately after building the first
  version: each slide was left in normal document flow, so the
  *carousel's own height* followed whichever slide happened to be
  showing, and the whole page below it visibly jumped up and down every
  ~4.5s as it rotated between a one-line and a two-line achievement.
  Slides are now absolutely positioned inside a fixed-min-height box —
  swapping which one is visible no longer changes the container's size
  at all.
- Fixed a second, unrelated real bug found alongside it: `.bottom-nav`
  had no `z-index` set, so a card near the bottom of a long page — the
  profile page's BMI card, via its `<abbr>` tooltip specifically — could
  render *over* the nav bar instead of under it on mobile. The nav now
  sits above ordinary page content/tooltips, below the floating
  training button and PR toasts (deliberately still topmost).
- Training mode's rest timer now plays a short two-tone chime
  (synthesized with the Web Audio API — no audio asset to ship) when
  the countdown reaches zero on its own; a manual "Skip rest" stays
  silent. A speaker icon next to the timer mutes/unmutes it, persisted
  in `localStorage` (a device setting, not a server-side preference).
- 3 new/changed UI strings translated across all 5 non-English catalogs
  (434 total, 0 fuzzy/untranslated). 23 new/changed tests (385 total).

**Fixed: a user's timezone preference was never actually applied
anywhere.**

- A real, significant bug: `User.timezone` (set on the profile page)
  was validated and stored correctly, but nothing in the app ever
  called `django.utils.timezone.activate()` — so every rendered
  date/time, and "today"/"this week" boundaries on the dashboard and in
  analytics, silently used `settings.TIME_ZONE` (UTC) for every user
  regardless of what they'd chosen. Fixed with a new
  `UserTimezoneMiddleware` (`apps.accounts.middleware`), the same
  after-auth-before-view placement and "re-derive from the database
  every request" pattern as the existing `UserLanguageMiddleware`.
- Also fixed the specific case that surfaced this: selecting
  "localtime" from the timezone dropdown looked like it should mean
  "use my device's own local time", but it's actually a fixed,
  non-dynamic `zoneinfo` alias (whatever `/etc/localtime` resolves to
  inside the container — UTC in this image) — a server-rendered app has
  no way to detect a visiting device's timezone without separate
  client-side plumbing this app doesn't have. Removed it (and tzdata's
  own "Factory" placeholder alias) from the picker; a real IANA zone
  (e.g. "Europe/Helsinki") now actually takes effect.
- 5 new tests (390 total).

**"Recently active" dashboard list** — who's been training and when,
shared across every user the same way the achievements carousel is.

- A new list (`apps.analytics.achievements.recently_active_users`) shows
  every opted-in user who has ever started a workout session, most
  recently active first, capped to 10 rows. Counts any session status,
  not just completed ones — starting a workout is itself a sign of
  activity, and it's what lets a still-in-progress session show as
  "Training now" (an accent-colored pulsing dot, same visual language as
  the training FAB's own indicator) instead of an ordinary elapsed-time
  reading. A session within the last 24 hours also gets a plain green
  dot as a secondary freshness cue.
- Relative time ("2 hours ago", "3 days ago") only needed one new
  translatable string for the wrapping phrase — the magnitude/unit part
  comes from Django's own built-in `timesince` filter, already
  translated into every locale this app ships as part of Django core.
- `User.show_achievements` now governs both the achievements carousel
  and this list (same privacy semantics as before — off hides *this*
  user's own data from everyone, themselves included); its label/help
  text were reworded from "achievements" to "activity" to reflect the
  broader scope.
- Found and fixed a real bug in the scratchpad translation-regeneration
  tooling used all session (not part of the committed app) while adding
  this feature's one new string with an embedded quote mark
  (`"Recently active"` inside a help text): its .po parser captured each
  line's content verbatim, including po's own backslash-escaping,
  without ever decoding it back to a real character — harmless for
  every previous string (none had a literal quote/backslash in them),
  but each regeneration re-escaped the already-escaped content further,
  reaching 7 backslashes deep before being caught. Fixed by decoding on
  read, so a read-then-write pass is actually idempotent.
- 12 new tests (403 total).

**Fixed: the rest timer's sound never played on iOS Safari.**

- Real cause, not a Safari quirk to work around blindly: iOS Safari (and
  other WebKit browsers) refuse to ever produce sound from a Web Audio
  `AudioContext` unless it was created/resumed *synchronously inside a
  genuine user gesture* — but `beep()` only ever ran from a
  `setInterval` callback (the countdown reaching zero) or an
  `HX-Trigger` event handled well after the "Log set" tap that caused
  it, and the original implementation created a brand-new `AudioContext`
  fresh inside `beep()` itself every single time, which is silent there
  no matter what.
- Fixed by creating one `AudioContext` lazily, once, and unlocking it
  (scheduling a near-silent blip — `resume()` alone isn't reliably
  enough on iOS specifically) on the very first tap/touch anywhere on
  the page, which is guaranteed to happen before any rest period could
  ever finish, since reaching training mode at all means the user just
  tapped something. `beep()` now reuses that same unlocked context for
  the rest of the page's life instead of creating a fresh, never-unlocked
  one each time.
- Pure client-side JS — no new tests (this app has no browser-level test
  runner), verified by inspecting the rendered page and checking the
  script's syntax directly.

**Fixed: "the charts are missing their bars" — a service-worker caching
bug, not a chart bug.**

- The report ("bars missing" — the exact same symptom fixed once already
  earlier this session) traced to something else entirely: rendering
  the same analytics page directly against the live server showed
  perfectly correct `<rect>` markup and CSS the whole time. The real
  cause was `static/sw.js`'s fetch handler, which cached every static
  asset **pure cache-first** — once `base.css` was cached once, it was
  served from that cache *forever*, the network never consulted again
  for it at all. Since static files here aren't served at content-hashed
  URLs (no `ManifestStaticFilesStorage`), this meant a whole session's
  worth of CSS fixes were permanently invisible to any browser (or
  installed PWA) that had already cached an old `base.css` — the bar
  chart CSS just happened to be the one someone actually noticed missing.
- Fixed by switching to stale-while-revalidate: the cached copy is still
  served immediately (fast, works offline), but every request also
  refetches in the background (`event.waitUntil` keeps the worker alive
  for it) and updates the cache for the *next* request — a deployed fix
  now reaches every browser within one extra load instead of never.
  `STATIC_CACHE`'s version string was also bumped once, forcing every
  existing installation to discard its stale cache immediately rather
  than only self-healing gradually.
- 1 new test (404 total) confirming the fetch handler no longer matches
  the old cache-first shape.

**A real, public API** — explicitly requested, and explicitly crossing
`docs/ARCHITECTURE.md`'s original "no REST/DRF API... until an actual
client needs it" guidance, so this one started with two clarifying
questions rather than an implementation: Django REST Framework vs.
hand-rolled, and full resource scope now vs. a smaller first pass. Both
answered with the recommended option — DRF, and every resource area at
once. See `docs/API.md` for the complete picture; summary of what
shipped:

- **Authentication**: per-user API keys (`Authorization: Bearer <key>`),
  never session/cookie auth. A key's secret is shown exactly once at
  creation and stored only as a SHA-256 hash.
- **Authorization**: every key carries independent Create/Read/Update/
  Delete flags *per context* (profile, exercises, programs, workouts,
  measurements, activities, records, analytics) — a key scoped to
  "programs: read" can't touch exercises at all, and can't write
  programs either, checked fresh on every request.
- **Rate limiting**: every key belongs to an admin-editable
  `RateLimitTier` (requests/minute + requests/day) — editing a tier's
  numbers in Django admin takes effect on every key on that tier
  immediately, no redeploy. Seeded with three tiers (Basic/Standard/
  Extended); counters live in a new `django_cache` database table
  (Django's `DatabaseCache` backend) rather than the in-memory default,
  since gunicorn's multiple worker processes don't share memory — an
  in-memory counter would silently allow `worker_count` times the
  configured rate.
- **Key management**: self-service from Profile → "API keys" — create
  (name + an 8-context × 4-verb permission grid), list, revoke. Capped
  at `ApiSettings.max_api_keys_per_user` (admin-editable, default 10)
  keys per user.
- **Endpoints**: full CRUD (where it makes sense) across all 8 contexts
  — exercises, programs/workouts/prescriptions, workout session logging
  (including a real "log a set" endpoint that runs PR detection exactly
  like the web UI's own does), measurement/activity types and entries,
  read-only personal records, and read-only analytics summaries/
  achievements. Every endpoint calls the exact same `apps/*/services.py`
  functions the server-rendered web views already use — never a second
  copy of ownership-scoping or domain logic. All weights/lengths are
  canonical units (kg/meters), never converted to a display-unit
  preference — a deliberate choice for an unambiguous machine contract,
  documented in `docs/API.md` "Canonical units, always".
- New `apps.api` app: models (`ApiKey`, `ApiKeyPermission`,
  `RateLimitTier`, `ApiSettings`), DRF authentication/permission/
  throttle classes, serializers/viewsets for every context, a
  self-service key-management UI, and Django admin coverage for
  everything admin-adjustable.
- 26 new/changed UI strings translated across fi/sv/ru/it/et (463 total,
  0 fuzzy/untranslated). 52 new tests (456 total).

**Django admin re-themed to match IronStack**, instead of a hand-built
parallel admin page — asked as an explicit either/or, answered with a
recommendation and reasoning before implementing (see
`docs/ARCHITECTURE.md` "Admin site" for the full case): a custom admin
UI would duplicate list views, filters, inline editing, and permission
checks Django's own admin already provides correctly, and every future
model would then need admin coverage written twice.

- `static/css/admin_theme.css` overrides Django admin's own CSS custom
  properties (a supported customization point since Django 4.x/5.x) to
  match `static/css/base.css`'s palette — applied to all three of
  admin's theme states (light/dark/OS-preference) identically, since
  IronStack itself has no light/dark toggle of its own; the
  now-meaningless toggle is hidden.
- Branding ("IronStack" instead of "Django administration") set in
  `apps.core.admin`; `templates/admin/base_site.html` adds the CSS link
  and a matching favicon, otherwise identical to Django's own template.
- 3 new UI strings translated across fi/sv/ru/it/et (466 total,
  0 fuzzy/untranslated). 3 new tests (459 total).

**Admin link on profile + messages unified into toasts.**

- Profile now shows an "Admin" card-link to `/admin/`, visible only to
  `is_staff` users (the same check Django's own admin login gate uses).
- The Django messages framework no longer renders as a permanent card in
  `<main>`; it now shares `#pr-toast-container` and the `.pr-banner`
  markup/behavior with PR notices — same auto-dismiss-after-6s, same
  close button. Error-tagged messages get a new `.pr-banner-error`
  (red) variant instead of the default success styling.
- 2 new UI strings translated across fi/sv/ru/it/et (468 total,
  0 fuzzy/untranslated). 3 new tests (462 total).

**Bottom-nav "Home"/"Progress" both lit up on the Progress page** — a
real bug: the Home link's active check compared
`request.resolver_match.url_name`, but Django's core dashboard and the
analytics dashboard both happen to be named `"dashboard"` in their own
app — `url_name` drops the namespace, so it matched on *both* pages.
Fixed by checking `resolver_match.view_name` (namespaced) instead,
applied to the Profile link too for the same latent reason. 2 new
regression tests (464 total).

**Plain text links restyled as CTA buttons**, continuing the "'Back to
X' links restyled as buttons" fix above to every remaining bare `<a>`
in the app (Cancel on program delete, row-level Edit links in workout/
measurement/activity history tables, exercise-list pagination, "View
current PRs") — all now `.button-secondary`, with any surrounding
context kept as plain text next to the button rather than folded into
its label. Login/signup's combined link+prose ("Already have an
account? Log in") was split into muted question text plus a short CTA
button, matching how the rest of the app pairs a link with nearby
text. The mobile bottom-nav and the `.range-filter` date-range tabs
were deliberately left alone — the nav is out of scope per explicit
request, and the range filter is a segmented tab control (own pill
styling, active-state fill), not a text link. 2 new UI strings
translated across fi/sv/ru/it/et (469 total, 0 fuzzy/untranslated).

**Profile page: greeting instead of a static card, explicit CTA
buttons on every action card, and a new "Account details" page.**

- The page used to open with a plain, permanent "Signed in as X" card.
  Replaced with a varied, time-of-day-aware greeting
  (`apps.core.greetings.random_greeting`) that mixes encouragement and
  light humor — a random pick from a 4-line pool keyed to morning/
  afternoon/evening/night in the user's own active timezone, no card
  wrapper.
- "Change password", "API keys", and (staff-only) "Admin" used to each
  be one giant `<a class="card card-link">` — the whole card was
  clickable but nothing about a plain card visually said so. Each is
  now a `.card-action-row`: description text on the left, a single
  explicit `.button-secondary` on the right as the only actual link.
- New "Account details" page/card (`apps.accounts.AccountDetailsView`,
  next to "Change password") — username, first/last name, and email,
  kept deliberately separate from both the preferences form and the
  password-change flow (which keeps its own re-authentication
  requirement).
- 21 new UI strings translated across fi/sv/ru/it/et (489 total, 0
  fuzzy/untranslated — 16 of them the greeting pool itself). 10 new
  tests (479 total, full suite green).

**Three more muscle groups seeded: Traps, Lats, Obliques.**

The original 11 seeded muscle groups left out three that mainstream
fitness apps usually split out on their own rather than folding into a
broader neighbor. Each new group ships with one new exercise of its
own (Barbell Shrug, Straight-Arm Pulldown, Side Plank) rather than
retagging an existing one — `Exercise.primary_/secondary_muscle_groups`
is live, current-state metadata that analytics reads directly, not a
historical snapshot, so retagging an existing exercise would have
retroactively shifted past muscle-group volume charts. 6 new UI
strings translated across fi/sv/ru/it/et (495 total, 0 fuzzy/
untranslated). 2 new tests.

## Local development

```bash
cp .env.example .env   # then edit secrets
docker compose up --build
```

This starts PostgreSQL, the Django dev server (`runserver`, auto-reload) at
http://localhost:8000, and nginx in front of it on http://localhost.

Run tests:

```bash
docker compose exec web pytest
```

Create an admin user:

```bash
docker compose exec web python manage.py createsuperuser
```

### Without Docker

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env  # set POSTGRES_HOST=localhost
python manage.py migrate
python manage.py runserver
```

## Production

`docker-compose.override.yml` is a **local development** file that Docker
Compose merges in automatically. Do not ship it to a server. On the
production host, only `docker-compose.yml` should be present, and `.env`
must set real secrets, `DJANGO_ALLOWED_HOSTS`, etc. — see
`docs/SECURITY.md`.

**Before your first production deploy**, read `docs/SECURITY.md`'s "TLS"
section — the bundled nginx config is HTTP-only, and the default
`DJANGO_SECURE_SSL_REDIRECT=true` will redirect-loop until you either put
a TLS-terminating proxy in front of this stack or explicitly opt out.

```bash
docker compose -f docker-compose.yml up -d --build
```

## Tests & linting

```bash
ruff check .
pytest
```
