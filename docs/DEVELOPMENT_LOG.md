# Development Log

The detailed, ongoing build history of IronStack — every phase and
every feature since, in the order it actually happened, including what
broke, what got caught during manual verification, and why a given
approach was chosen over the alternatives. This is the file
`CHANGELOG.md` points to for "how did this actually get built", while
`CHANGELOG.md` itself stays a terse, version-bucketed "what changed"
summary. `README.md` stays a short orientation for someone new to the
project — this file is for anyone who wants the full story.

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

**Dashboard greeting, explicit CTA buttons on every profile action
card, and a new "Account details" page.**

- The profile page used to open with a plain, permanent "Signed in as
  X" card. Replaced with a varied, time-of-day-aware greeting
  (`apps.core.greetings.random_greeting`) that mixes encouragement and
  light humor — a random pick from a 4-line pool keyed to morning/
  afternoon/evening/night in the user's own active timezone. Then
  moved again, this time to the dashboard (`.dashboard-greeting`,
  right below the "IronStack" heading) — Home is the page a user
  actually lands on, so that's where a greeting belongs, not a
  preferences page.
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

**Application version, single source of truth for future backup/
restore tooling.** A plain-text `VERSION` file at the repo root (not a
hardcoded Python constant, not derived from git) is read and cached by
`apps.core.version.get_version()`; `apps.core.context_processors.
app_version` exposes it in every template's context. Shown today in
the profile page footer (`.app-version`); intended to be the same
value a future backup archive stamps itself with and a future restore
path checks against, without either needing to import Django — see
`docs/ARCHITECTURE.md` "Versioning". 1 new UI string translated across
fi/sv/ru/it/et (496 total, 0 fuzzy/untranslated). 3 new tests.

**More release metadata for v1.0.0: git commit, migration state, a
`version_info` command, `CHANGELOG.md`.**

- `apps.core.version.get_git_sha()` reads an image-baked `GIT_SHA`
  file (never committed — a build artifact, not source), distinct
  from `VERSION` since two builds can share a version number but
  never a commit. `scripts/build.sh` is the optional build path that
  actually fills it in (along with `APP_VERSION`/`BUILD_DATE`) via
  Docker build-args, which the `Dockerfile`'s runtime stage turns into
  both that file and standard OCI image labels
  (`org.opencontainers.image.revision`/`.version`/`.created` —
  inspectable via `docker image inspect`). The plain `docker compose
  up -d --build` from `README.md`'s own "Production deployment"
  section still works unchanged — everything just defaults to
  "unknown" instead.
- `apps.core.version.get_migration_state()` reads Django's own
  migration-recorder table — the real compatibility signal for
  whether a database backup is safe to load into a given code
  version, which a version string alone can't answer.
- `python manage.py version_info [--pretty]` bundles version/git
  commit/migration state/timestamp into one JSON blob — the intended
  call a future backup script makes to stamp an archive, and a future
  restore path makes to check a backup against the instance it's
  restoring into.
- New `CHANGELOG.md` (Keep a Changelog style) maps version numbers to
  what changed, distinct from this file (the detailed, ongoing build
  log).
- 6 new tests (487 total, full suite green). No new UI strings —
  none of this is user-facing chrome.

**Backups: host-side scripts, plus a web UI for admins.** See
`docs/BACKUP.md` for the full picture — two independent mechanisms:

- `scripts/backup.sh`/`restore.sh` — run from the Docker host, the
  same `pg_dump`/`pg_restore` approach as above but reachable through
  `docker compose exec`'s local socket access to `db`; restore stops
  `web` first, so nothing is using the database while it's replaced.
- Profile → Administration → Backups (`apps.core.backups`, admin-only)
  — create/download/restore without leaving the app, stored in a new
  `backups_data` volume. Restore here is real riskier — asked
  explicitly and confirmed before building it, since the request
  handling it is itself using the very database connection about to
  be replaced. Restores into a freshly created, differently-named
  database first and only swaps it in for the live one
  (`ALTER DATABASE ... RENAME`) once the new data has actually loaded
  — an earlier version dropped-then-restored-in-place, and a
  `pg_restore` failure partway through (a client/server version
  mismatch, caught during testing) left the live database completely
  empty with nothing to fall back on.
- The profile page's staff-only "Admin"/"Backups" cards now sit in a
  `.danger-zone` (red-bordered box, red heading) — set visually apart
  from the plain cards above them.
- 20 new UI strings translated across fi/sv/ru/it/et (516 total, 0
  fuzzy/untranslated). 17 new tests (504 total, full suite green).

**A privacy toggle for first names, and greetings that use yours.**

- New profile setting "Show my name to others" (`User.
  show_name_to_others`, on by default) — a second, more granular
  privacy control layered on top of `show_achievements`: that one
  decides whether a user's data appears in the achievements carousel/
  "Recently active" list *at all*; this one only decides whether their
  first name is part of it once it does. `User.public_display_name()`
  is "username (First name)" when on and a first name is set, the bare
  username otherwise — the username itself was already shown
  everywhere `show_achievements` applies, so there's nothing left to
  hide with this one off.
- The dashboard greeting (`apps.core.greetings`) now addresses a user
  by first name if they've set one — unaffected by the new toggle,
  since that only governs what *other* users see, and a greeting is a
  user looking at their own name.
- The achievements API endpoint's `AchievementSerializer` field is
  renamed `username` → `display_name` to match — a small breaking
  change to that response shape, made now while the API has no
  established external consumers yet.
- 2 new UI strings translated across fi/sv/ru/it/et (518 total, 0
  fuzzy/untranslated). 12 new tests (516 total, full suite green).

**BMI moved from the dashboard/profile to the "Body weight" page.**

BMI only ever meant anything alongside a logged body weight, and the
"Body weight" measurement history page (`apps.measurements`) is where
one actually gets logged — so that's where the number (once
computable) and the WHO category ranges table live now, right above
the log-a-reading form, instead of the dashboard's old three-nudge-
card chain ("add your height" → "log a body weight" → the actual
card) or the profile page's unconditional copy of the same table. The
`show_bmi`/`height` settings themselves stay on the profile page —
only the card/table moved, gated the same way it always was
(`show_bmi`, and now specifically the system "Body weight" type — any
other measurement, e.g. waist or a custom type, never shows it).
`templates/core/_bmi_card.html`'s own "not enough data yet" fallback
covers every missing-data sub-state, so no separate nudge cards were
needed on the new page. 1 new UI string translated across fi/sv/ru/
it/et (514 total, 0 fuzzy/untranslated — net fewer than before, since
the three old nudge-card strings no longer exist). Tests moved from
`apps.core`/`apps.accounts` to `apps.measurements` alongside the
feature (514 total, full suite green).

**A changelog viewer, and `CHANGELOG.md` kept current.**

- `CHANGELOG.md` now has an `[Unreleased]` section (Keep a Changelog
  style) for everything that's landed since the `[1.0.0]` entry —
  backups, the name-privacy toggle, and the BMI move above.
- Clicking the version number on the profile page opens a modal
  showing that whole file — every past version and what changed,
  without leaving the app. `apps.core.changelog` renders it with a
  small, deliberately narrow Markdown-subset parser (headings, bullets
  with soft-wrapped continuation lines, `` `code` ``/`**bold**`/
  `[links](url)`) scoped to exactly what `CHANGELOG.md` actually uses,
  rather than a full Markdown library — that file is the only thing
  that writes it, so there was no real parsing surface to cover.
  Result is cached the same way `apps.core.version.get_version()` is.
- 1 new UI string translated across fi/sv/ru/it/et (515 total, 0
  fuzzy/untranslated). 8 new tests (522 total, full suite green).

**A production-readiness pass** — asked "what's left before this is
production ready", answered with six concrete gaps, then asked to
close all of them. See `docs/SECURITY.md` and `docs/BACKUP.md` for the
full detail on each.

- **TLS**: `docker-compose.tls.yml`, a ready-to-use overlay replacing
  the bundled HTTP-only nginx with Caddy (`compose/caddy/Caddyfile`),
  which provisions and renews a real Let's Encrypt certificate on its
  own given just a domain name — the "recommended path"
  `docs/SECURITY.md` already described, now copy-paste-ready instead
  of prose you'd have to build yourself.
- **Password reset**: `django.contrib.auth`'s URLs were already wired
  up, but with no templates (`templates/registration/password_reset_*.
  html`, `password_reset_email.html`, `_subject.txt` — six new files)
  and no `EMAIL_BACKEND` configured at all, visiting `/accounts/
  password_reset/` would 500. `DJANGO_EMAIL_HOST` (`.env.example`)
  switches from a console-logging fallback to real SMTP; `DJANGO_ADMINS`
  additionally opts into Django's own built-in `mail_admins` error
  reporting for uncaught exceptions — deliberately not Sentry: once
  SMTP is configured for password reset anyway, error emails are free,
  no new dependency.
- **Login brute-force protection**: `apps.accounts.forms.
  RateLimitedAuthenticationForm` blocks further attempts from the same
  client IP for 15 minutes after 5 failures within that window — a gap
  distinct from `apps.api`'s per-key rate limiting, which never
  touched the session-based web login at all.
- **Signup gating**: `DJANGO_SIGNUP_ENABLED=false` closes self-service
  registration, gating the URL itself rather than just hiding the
  login page's link to it.
- **Automatic backups**: a new `backup-scheduler` service
  (`docker-compose.yml`) runs `manage.py create_backup` once a day,
  on by default in production, off by default in local dev (a
  `manual` Compose profile). Both mechanisms from the previous backup
  work were manual-trigger only.
- 33 new UI strings translated across fi/sv/ru/it/et (534 total, 0
  fuzzy/untranslated). 21 new tests (543 total, full suite green) —
  all six gaps were also live-verified end to end (a real password
  reset email through to logging in with the new password, an actual
  lockout after 5 failed logins, signup gated then live-tested
  disabled, real backup archives created by the new command), not just
  covered by the automated suite.
- **Admin-adjustable backup settings**: the automatic scheduler's hour,
  on/off state, and retention count used to be fixed by `BACKUP_HOUR`
  at container start; now stored in the database
  (`apps.core.models.BackupSettings`, the same `pk=1` singleton pattern
  as `apps.api.models.ApiSettings`) and editable from a "Settings" card
  on Profile → Administration → Backups (or `/admin/`), taking effect
  the same day without restarting `backup-scheduler`. Retention is
  enforced automatically after every backup, scheduled or manual
  (`apps.core.backups.prune_backups`) — `0` keeps every backup forever.
  See `docs/BACKUP.md` "Adjusting the schedule". 9 new UI strings
  translated across fi/sv/ru/it/et (543 total, 0 fuzzy/untranslated).
- **User feedback**: any signed-in user can now submit feedback (a
  category — Workouts/Programs/Progress/Body measurements/Activities/
  Account & profile/Other — plus a subject and a free-text message)
  from a new "Feedback" card on the profile page
  (`apps.core.models.Feedback`, `apps.core.views_feedback.
  FeedbackCreateView`). Visible only to staff, from a matching
  "Feedback" card under Profile → Administration → Feedback
  (`FeedbackListView`) or `/admin/` — never to other regular users. A
  one-way inbox, not a two-way conversation thread. Whether submissions
  are currently open is itself an admin-adjustable singleton toggle
  (`apps.core.models.FeedbackSettings`, the same `pk=1` pattern as
  `BackupSettings`/`ApiSettings`) with its own settings card on that
  same staff-only page — turning it off only closes new submissions,
  gated in the view itself (not just the profile card's link) the same
  way `DJANGO_SIGNUP_ENABLED` gates signup; feedback already on file
  stays visible to staff either way. `StaffRequiredMixin` (the
  `is_staff` check every admin-only page under `apps.core` uses) moved
  out of `views_backup.py` into its own `apps/core/mixins.py` so this
  reuses it instead of redefining it. 16 new tests. 17 new UI strings
  translated across fi/sv/ru/it/et (560 total, 0 fuzzy/untranslated).
- **A second production-readiness pass**, prompted by asking "what's
  still missing?" again after the first one (backups/email/rate-
  limiting/signup-gating/TLS): see `docs/SECURITY.md` for all four in
  full.
  - **`CSRF_TRUSTED_ORIGINS`**: now derived from `DJANGO_ALLOWED_HOSTS`
    automatically in `config.settings.production` — without it, a
    reverse-proxied deployment can hit "CSRF verification failed" on
    real form submissions the moment anything about the proxy/TLS
    setup doesn't line up exactly with what Django infers on its own.
  - **Password-reset rate limiting**: `PasswordResetView` had the same
    brute-force gap login did before the first pass — nothing stopped
    it being used to spam an arbitrary address with reset emails via
    this instance's own SMTP relay.
    `apps.accounts.forms.RateLimitedPasswordResetForm` closes it, same
    5-per-15-minutes-per-IP shape as the existing login limiter.
  - **`Content-Security-Policy` header**: `apps.core.middleware.
    ContentSecurityPolicyMiddleware`, hand-rolled rather than adding
    django-csp (one fixed policy string, not per-view nonces). Shipping
    it for real (not just `'unsafe-inline'`-everything, which would
    defeat most of its point) meant actually removing every inline
    `<script>` block (moved to `static/js/*.js`) and every native
    `onclick=`/`onsubmit=` attribute (converted to Alpine's own
    `x-data`/`@submit`/`@click` directives, which aren't a native
    inline-script mechanism CSP restricts) across the whole template
    layer — `script-src`/`style-src` still need `'unsafe-eval'`/
    `'unsafe-inline'` respectively (Alpine's expression evaluator;
    plain `style="..."` attributes), everything else is `'self'`-only.
  - **Dependabot**: `.github/dependabot.yml` — weekly, reviewed PRs for
    outdated pip/Docker/GitHub Actions dependencies, still gated by the
    existing CI. No new runtime dependency, plain GitHub config.
  - 7 new tests (581 total, full suite green). 1 new UI string
    translated across fi/sv/ru/it/et (561 total, 0 fuzzy/untranslated).

**Published Docker images — GitHub Container Registry, published on
every push to master.** Production used to mean either building the
image on the server itself (`docker compose up -d --build`) or running
`scripts/build.sh` locally and shipping that image over — now CI does
it once, centrally, and every server just pulls.

- A new `publish-image` job in `.github/workflows/ci.yml`, gated with
  `needs: lint-and-test` and `if: ... github.ref == 'refs/heads/master'`
  — only a push that's both landed on master *and* passed lint/tests
  gets published, never a pull request or another branch, so
  `:latest` always reflects code that's actually green. Pushes to
  `ghcr.io/jonnesaloranta/ironstack`, tagged `:latest` and the repo's
  own `VERSION` file's value (e.g. `:1.1.0`) — both tags, every time.
  Uses `GITHUB_TOKEN` (`packages: write`, requested only on this job —
  the workflow's top-level `permissions` default to `contents: read`
  otherwise) rather than a new PAT secret. Same `GIT_SHA`/`APP_VERSION`/
  `BUILD_DATE` build-args `scripts/build.sh` already passed for a local
  build, so the published image's `GIT_SHA` file and OCI labels
  (`docs/ARCHITECTURE.md` "Versioning") are populated the same way.
- `docker-compose.yml`'s `web`/`backup-scheduler` gained an `image:`
  pointing at that same tag (`${IRONSTACK_IMAGE_TAG:-latest}`, pinnable
  via `.env`) — `docker compose pull && docker compose up -d` now works
  standalone, no build step on the production host at all. `build:`
  stays too, unchanged, as a local-build fallback (both `docker compose
  up --build` and `scripts/build.sh` keep working exactly as before)
  since `image:` + `build:` together is Compose's own supported
  pattern for "prefer the named image if present, otherwise build it."
- Real risk caught before it could bite: adding `image:` to the base
  compose file means `docker-compose.override.yml` (always merged for
  local dev) would otherwise inherit that same tag for a *dev* build
  too — a `docker compose up --build` locally would then tag the
  result identically to the real published image. This exact failure
  mode had already happened once before this session (`scripts/build.sh`
  overwriting the dev image because both shared one tag then). Fixed
  by giving the dev override its own distinct `image: ironstack-dev`
  for both `web` and `backup-scheduler`, verified via `docker compose
  config` (and `--profile manual config` for the latter) actually
  resolving to the two different names as expected.
- No new tests (pure infra — Compose/CI config, no Python/UI change);
  verified with `docker compose config`/`docker compose -f
  docker-compose.yml -f docker-compose.tls.yml config` against every
  compose file combination instead. `docs/ARCHITECTURE.md`
  "Versioning" and `README.md`'s "Production deployment" both updated.

**Verified the Dependabot-proposed dependency bumps, then reconciled
the rest of the repo to match.** Four Dependabot PRs were merged
directly on GitHub (`actions/checkout` 4→7, `actions/setup-python`
5→7, the Dockerfile's `python:3.12-slim`→`3.14-slim`, and a grouped
bump of Django 5.0→6.1, psycopg 3.1→3.3.4, gunicorn 22→26,
djangorestframework 3.15→3.18, pytest 8→9.1.1, pytest-django 4.8→4.14,
factory_boy 3.3→3.3.3, ruff 0.5→0.16.2) — real major-version jumps on
several of these, not the patch/minor bumps Dependabot's config
otherwise mostly proposes, and enough of them bundled together that
"it's just Dependabot" wasn't a safe enough reason to trust without
actually running the suite.

- Rebuilt the image from a clean `--no-cache --pull` (had to
  `docker builder prune`/`docker image prune` first — the host had
  filled its disk from this session's accumulated build cache) and ran
  the full suite against it for real: ruff clean, no missing
  migrations, all 581 tests green, live-verified pages/admin/API
  endpoints all still responding correctly. A stray finding during
  verification that turned out to be a red herring, not a bug: `/app/
  .venv` inside the container looked like it still had a `python3.12`
  layout even after the rebuild — actually a stale directory left over
  on the *host* from early in this project's life, bind-mounted in by
  `docker-compose.override.yml`'s `.:/app` and irrelevant to the
  image's real venv (`/venv`, per the `Dockerfile`, correctly showing
  `python3.14` throughout).
- Dependabot's own per-file scanning has no way to know that the
  Dockerfile's `FROM` tag and `.github/workflows/ci.yml`'s
  `setup-python` `python-version:` string are the same logical
  version — it left the workflow pinned at 3.12 while the image moved
  to 3.14, silently reintroducing exactly the "keep dev/CI/prod on one
  version" gap this project has otherwise cared about (see the
  Postgres-version comment already in `ci.yml`). Fixed by bumping
  `ci.yml`'s `python-version` to `"3.14"` too, plus every other place
  that named the old versions: `pyproject.toml`'s ruff
  `target-version` (`py312`→`py314`), `README.md`'s badges and
  `python3.12 -m venv` command, and `docs/DEVELOPMENT.md`'s pinned-
  versions list (also dropped the `(LTS)` label next to Django, since
  that specific claim isn't actually known to hold for 6.1).

**Automatic GitHub Releases on every version bump, using CHANGELOG.md
as the release notes.** A new `create-release` job in
`.github/workflows/ci.yml`, chained after `publish-image`
(`needs: [lint-and-test, publish-image]`) so a release only ever
points at a commit that's both green and has a real Docker image
published for it.

- No new trigger, no separate `git tag` step: bumping the repo-root
  `VERSION` file and pushing to master was already this project's
  entire release procedure (`docs/ARCHITECTURE.md` "Versioning") —
  the job diffs `VERSION` between the commit before this push and the
  one it lands on, and only proceeds if it actually changed. Guards
  the edge case where `github.event.before` is the all-zero SHA (a
  branch's very first push, or a rewritten history) by treating that
  as "not a version bump" rather than failing.
- Release notes are read straight out of `CHANGELOG.md`'s own section
  for that version (the same `## [X.Y.Z]` heading `apps.core.changelog`
  already parses for the profile page's changelog modal) — nothing
  duplicated into the workflow file itself. A version with no matching
  section still gets a release, with a placeholder body pointing at
  the commit history, rather than silently skipping it or failing the
  whole job over a documentation gap.
- `VERSION`'s content is validated as a plain `X.Y.Z` string before
  anything else runs — a malformed value fails loudly instead of
  creating a release tagged `vunknown` or similar.
- Verified everything short of an actual live version bump (out of
  scope to trigger just to test this): full YAML parse, and the
  extraction/trim/semver-check shell logic run directly against this
  repo's own real `CHANGELOG.md`, confirming a clean, correctly
  leading/trailing-blank-trimmed section for `1.1.0` and the right
  fallback behavior for a version with no section at all.

**Restore from an uploaded backup file, not just one already on the
server.** The web UI could already download and restore backups it
had created itself, but had no way to restore from a `.tar.gz` an
admin had downloaded earlier and now had sitting on their own
computer — asked directly ("I want to upload a backup I downloaded
earlier and restore from it").

- A new "Upload backup" card (Profile → Administration → Backups)
  takes a `.tar.gz` file (`apps.core.forms.BackupUploadForm`,
  `enctype="multipart/form-data"`) and, once
  `apps.core.backups.save_uploaded_backup()` confirms it's a real
  backup archive (readable, all three of `database.dump`/`media.tar`/
  `manifest.json` present — rejected with a clear form error
  otherwise, before anything touches `BACKUP_DIR` at all), stores it
  under a fresh, server-generated name and redirects straight into the
  exact same restore-confirmation page a server-created backup uses —
  upload is just a second way to get a valid archive into `BACKUP_DIR`,
  nothing about actually restoring one is different. The stored
  filename never uses whatever the browser sent (same "don't trust
  anything from the request" reasoning `safe_archive_path()` already
  applies to a restore/download target).
- `save_uploaded_backup()` writes with `shutil.copyfileobj` rather
  than Django `UploadedFile`'s own `.chunks()` — works the same for a
  real multipart upload and for a plain file-like object, so the
  function doesn't assume anything Django-specific about its input
  beyond `read()`/`seek()`. Caught during testing: the first version
  used `.chunks()` and broke every service-level test that passed a
  plain `io.BytesIO` directly, rather than wrapping it in a real
  Django upload object just to satisfy that one method.
- `compose/nginx/nginx.conf`'s `client_max_body_size` raised from 20M
  to 200M — a real backup can grow well past the old limit once
  media/logged data exist, and 20M was already an arbitrary number,
  not load-bearing for anything else.
- 9 new tests. 8 new UI strings translated across fi/sv/ru/it/et (569
  total, 0 fuzzy/untranslated). Live-verified end to end in Finnish: a
  real multipart upload of a hand-built valid archive redirected
  straight to its restore-confirmation page showing the archive's own
  manifest; a garbage upload was rejected with the translated form
  error and nothing was written to `BACKUP_DIR`.

**Backup metadata, deletion, and a dedicated "Automatic backups"
section separate from manual/uploaded ones.** Asked directly: show
more about each backup (app version, ...), let admins delete one from
the list, and distinguish automatic backups from the rest — asked
again, more specifically, once the first pass only added an inline tag
to one shared list: split them into two clearly separated sections
instead, and fix mobile layout issues (row content overflowing the
screen, inconsistent row heights) the richer per-row content had
introduced.

- `apps.core.backups.create_backup()` gained a `source` parameter
  (`"manual"` by default) recorded straight into the archive's own
  `manifest.json` — `apps.core.management.commands.backup_scheduler`
  is the only built-in caller that ever passes `"scheduled"` (via a
  new `--source` option on the `create_backup` management command).
  `list_backups()` reads it back per backup (along with `version`/
  `git_sha`, already in every manifest from the versioning work) to
  drive both the "Version" line and the source tag — falling back to
  `"manual"` for a backup made before this field existed, or for a
  manifest that can't be read for any reason, rather than breaking the
  whole list page over one backup's own metadata. Uploaded backups are
  tagged `"uploaded"` purely from their filename prefix
  (`save_uploaded_backup()`'s own naming) — their manifest belongs to
  whatever instance originally created the archive and has no way to
  record how it later got here. A `git_sha` of the literal string
  `"unknown"` (`apps.core.version.get_git_sha()`'s own fallback for an
  image that skipped `scripts/build.sh`) is deliberately not shown at
  all — a bare "(unknown)" next to a version number reads as an error,
  not useful info, unlike the labeled "Git commit: unknown" the
  restore-confirm page already showed elsewhere.
- `apps.core.views_backup.BackupListView` now hands the template three
  separate things: `newest_scheduled` (one backup or `None`),
  `older_scheduled_backups` (collapsed by default behind a "▾ Show N
  earlier automatic backups" toggle — Alpine `x-show`, no server
  round-trip), and `manual_backups` (everything else, always shown
  individually). `templates/core/backup_list.html` renders these as
  two headed sections, "Automatic backups" and "Manual & uploaded
  backups", rather than one interleaved list with an inline tag — a
  daily schedule produces a fundamentally different kind of list
  (many, similar, low-effort) than a person deliberately clicking a
  button or uploading a file (few, each intentional), and the two
  deserve visual separation, not just a tag to tell them apart within
  one list.
- A new `BackupDeleteView`/`apps.core.backups.delete_backup()` —
  non-destructive to anything actually running (only ever discards a
  copy in storage), so just a plain confirm-then-POST like any other
  delete in this app, no manifest-comparison confirm page of restore's
  own.
- `templates/core/_backup_row.html` extracted so both sections render
  each row identically without duplicating the markup.
- **Mobile layout, fixed twice after being reported broken on a real
  device.** First pass: the original 5-column table (Created/Size/
  Version/tag/3 action buttons) forced real horizontal overflow on
  narrow screens — reworked to a 2-column table (a single stacked info
  cell, an actions cell), `.set-actions` gained `flex-wrap: wrap` so
  its 3 buttons stack instead of forcing width, `.set-table td` gained
  `overflow-wrap: break-word`, and a new `.button-wrap` class let the
  toggle button's own label (long in several languages) wrap instead
  of forcing its own width. A second report (row bottom borders
  sitting at visibly different heights row to row, from `.set-table
  td`'s default `vertical-align: middle`) was patched with
  `vertical-align: top` — a real improvement, but still patching a
  table where sibling rows fundamentally have to share column widths
  and a row's own height depends on what's in *every* cell of that
  row, not just its own content.
  Second pass, asked directly ("put a box around each one and drop the
  line underneath them instead"): dropped the `<table>`/`.set-table`
  approach for this page entirely — `templates/core/_backup_row.html`
  now renders one `.card` per backup (the same per-item card pattern
  `templates/core/feedback_list.html` already uses), each sizing
  itself independently with no shared row/column grid to jitter
  against a neighbor's content in the first place. `.table-wrap`/
  `.set-table` stay exactly as they were for the *other* wide tables
  in the app (workout set logging, measurement/activity history) —
  only this page's markup changed, not the shared classes' own
  definitions (the `vertical-align`/`overflow-wrap` additions from the
  first pass stay too, still a real improvement for those).
- 12 new tests. 12 new UI strings translated across fi/sv/ru/it/et (577
  total, 0 fuzzy/untranslated). Live-verified in Finnish at each step:
  the version line (and its "(unknown)" hiding), both section headings
  and their own empty states, the collapse toggle actually hiding/
  showing the right rows, and a real delete redirecting with the
  correct translated success message.

**Two-factor authentication, plus login/signup branding and a
site-wide disclaimer footer.** Asked in one message: a 2FA setting on
the profile page; while at it, fix the login/signup pages missing the
IronStack logo/wordmark; and add an admin-editable disclaimer footer
on those same pages disclaiming responsibility for data loss.

- TOTP (RFC 6238), via `pyotp` (not `django-otp` — see
  `docs/SECURITY.md` "Two-factor authentication" for the reasoning)
  plus `qrcode[pil]` to render the setup QR code as an inline
  `data:image/png;base64,...` image, no separate image-serving
  endpoint. `User` gained `totp_secret`/`totp_enabled`; a new
  `TwoFactorBackupCode` model holds 10 single-use, password-hasher-
  hashed recovery codes per user. The secret is written to the user as
  soon as setup starts (Profile → "Two-factor authentication" → "Set
  up"), before it's confirmed — `totp_enabled` only flips to `True`
  once the confirmation code succeeds, so an abandoned setup attempt
  never blocks a future plain-password login.
- The login flow's second step, `TwoFactorVerifyView`
  (`RateLimitedLoginView.form_valid` detours here instead of calling
  `login()` when the user has 2FA enabled, staging their id in the
  session under `pre_2fa_user_id` until a correct code arrives) — its
  own rate limit (5 wrong codes / 5 minutes) is keyed by user ID
  rather than client IP, deliberately different from every other
  rate limit in this app: by this step the attacker already has a
  correct password and a specific account, so IP-keying alone would
  be trivially routed around. Accepts either a live TOTP code or one
  of the backup codes, disambiguated by whether the submitted value is
  6 digits.
  `TwoFactorManageView` consolidates "regenerate backup codes" and
  "disable" (password-gated) on one page, matching the existing
  "Account details"/"API keys" pattern of the profile linking out to a
  dedicated page per concern rather than crowding buttons onto the
  card itself. A new Django-admin action, "Disable two-factor
  authentication for selected users", is the recovery path for
  someone locked out with no working authenticator and no backup
  codes left — the one situation the self-service flows can't help
  with, since both require either the password and a working second
  factor, or the password alone but never a bypass of the second
  factor entirely.
- `templates/registration/_auth_brand.html` (the icon + "IronStack"
  wordmark) and `templates/registration/_auth_disclaimer.html` (the
  footer, rendered only when non-blank) are now included on both
  `login.html` and `signup.html`. The disclaimer text itself comes
  from a new singleton, `SiteDisclaimer` (same `pk=1`/`load()` pattern
  as `ApiSettings`/`BackupSettings`/`FeedbackSettings`), editable only
  from Django admin, with a sensible default and deliberately left
  untranslated — it's operator-facing legal text a self-hoster is
  meant to actually write themselves, not app chrome.
- Two real bugs caught by the new tests, not by manual curl testing:
  `TwoFactorSetupView`/`TwoFactorManageView`/`TwoFactorDisableView`
  each override `dispatch()` to redirect based on `request.user`
  state (already enabled, not enabled yet, ...) before ever calling
  `super().dispatch()` — but `LoginRequiredMixin`'s own
  authentication check *is* that `super().dispatch()` call, so an
  anonymous request crashed with `AttributeError` (`AnonymousUser` has
  no `totp_enabled`) instead of redirecting to login. Fixed by
  checking `request.user.is_authenticated` first, before touching any
  2FA-specific attribute, in all three. Also found (while still doing
  the earlier curl-based verification, before tests existed):
  `TwoFactorRegenerateBackupCodesView` returned 405 because it called
  `TemplateView.as_view(...)( request)` from inside another view's
  POST handler — that re-dispatches the *original* request through a
  second CBV's own method-routing, and a bare `TemplateView` has no
  `post()`. Fixed with a plain `django.shortcuts.render()` call
  instead.
- 39 new tests, covering the TOTP/backup-code service functions
  directly, the full setup/confirm/login-verify/disable/regenerate
  view flows (including both bugs above once fixed), the rate limiter,
  the admin recovery action, and the disclaimer/branding rendering
  (including blank-text-hides-the-footer). 36 new UI strings
  translated across fi/sv/ru/it/et (613 total, 0 fuzzy/untranslated).
  Live-verified end to end, twice: once by hand over curl with real
  TOTP codes computed inside the container (setup, login redirect,
  verify success/failure/lockout, backup-code login and single-use
  consumption, regenerate, password-gated disable, and the admin
  recovery action), and again in Finnish after translating (setup
  page, profile card, login page logo and disclaimer).

**A one-time onboarding modal for new users.** Asked directly: a
popup right after login, asking for email/name/weight/other settings,
explaining what each is used for. Deliberately skippable, not a
blocking gate — consistent with docs/PRODUCT_REQUIREMENTS.md's "the
user always has final control" — and every field is optional even on
the "Save" path itself, not just via the separate "Not now" button.

- `User.onboarding_completed` (default `False`) gates it. The
  migration that adds the field backfills `True` onto every account
  that already existed at that point (a `RunPython` step, not just
  the field default), so it never retroactively appears for someone
  already using the app — only for accounts created afterward.
- Shown via a new context processor,
  `apps.accounts.context_processors.onboarding` (same reasoning as
  the existing `apps.workouts.context_processors.
  active_workout_session`: the modal has to appear on whatever page a
  user happens to land on right after login, not one specific view,
  so gating it per-view would mean threading a flag through every
  view in the app instead of once, globally, in `base.html`).
- `OnboardingForm` is a plain `Form`, not a `ModelForm`: it writes to
  `User` (first name, email, unit system) and, for weight, straight
  into `apps.measurements.models.BodyMeasurement` under the same
  system "Body weight" type `apps.measurements`' own logging form
  uses — a weight entered here shows up on the Body weight history
  page like any other logged reading, not a separate "onboarding
  weight" field bolted onto `User`.
- HTMX-driven like every other form in this app: submitting "Save" or
  "Not now" (two buttons, one form, distinguished by an `action`
  value — skipping still has to mark the prompt seen, or it would
  just reappear on the very next page) posts to `OnboardingView`,
  which re-renders the same modal fragment either way — with field
  errors on a failed save, or as just its own empty wrapper `<div>` on
  success, so the modal disappears without a full page navigation.
- A real naming-collision bug caught before it shipped, not by a
  test: the modal's form was initially context-keyed as `"form"`,
  which the context processor merges into *every* page's context —
  including pages like Profile that already have their own
  view-specific `form` (`ProfileForm`) in context under that exact
  same name. Renamed to `onboarding_form` throughout.
- 14 new tests (model default, context-processor visibility across
  three states, save/skip/validation-error/login-required for the
  view, including the kg⇄lb weight-unit conversion and that a failed
  save's data is never persisted by a subsequent skip). Because the
  modal is now included on literally every page, the full suite (not
  just `apps.accounts`) was re-run end to end to check for incidental
  collisions with other pages' own content — all 658 tests (644
  existing + 14 new) pass unchanged. 11 new UI strings translated
  across fi/sv/ru/it/et (624 total, 0 fuzzy/untranslated).
  Live-verified over curl: the modal appearing on an unrelated page
  right after a fresh login, saving with a weight in both kg and lb
  (converted correctly to canonical kg), skipping, an invalid email
  being rejected without completing onboarding, a skip afterward
  correctly discarding that earlier invalid attempt rather than
  saving it, and the whole flow again in Finnish.

**Height added to the same onboarding prompt**, asked for right after
it shipped. A plain `User.height` field (same shape and cm/inch
conversion as `ProfileForm`'s own), not a repeated measurement like
weight — one-off context for BMI (`apps.core.bmi`), not something
logged over time. 3 new tests (metric and imperial conversion, and
that leaving it blank leaves `height` unset rather than zeroing it).
1 new UI string translated across fi/sv/ru/it/et (625 total, 0
fuzzy/untranslated) — the "Height (cm)"/"Height (in)" labels
themselves were already translated, reused from `ProfileForm`.
Live-verified: 180.5cm and 70in both round-tripped to the correct
canonical meters value, and the field renders correctly in Finnish.

**Cutting the 1.2.0 release surfaced two real bugs — one in the app,
one in the release pipeline itself.** `VERSION` bumped to `1.2.0`,
`CHANGELOG.md`'s `[Unreleased]` cut into a dated section, `dev` merged
into `master`.

- CI's `pytest` run (not `python manage.py test --keepdb`, which is
  what every local verification this session had used) failed:
  `BMIOnBodyWeightHistoryPageTests` in `apps.measurements` asserts the
  literal absence of "BMI" from the whole page when a user has BMI
  turned off — the onboarding modal's height field mentions "BMI" in
  its own help text, and that test's user, like any freshly created
  one, hadn't onboarded yet. Missed locally because the height field
  was only re-verified against `apps.accounts`'s own tests, not the
  full cross-app suite — the exact kind of collision the full-suite
  rerun after the *first* onboarding-modal commit was specifically
  meant to catch, skipped this time as "low risk" for what looked like
  a small, additive change. Fixed by creating that test's user
  already onboarded (the modal is irrelevant noise for what it
  actually checks), verified with a full `pytest -q` run matching CI
  exactly (661 passed).
- Pushing the fix surfaced a second, independent bug: CI went green
  end to end, but no release was created. `create-release`'s check for
  "did VERSION change" diffed against `github.event.before` — the
  commit immediately before *this* push, which was the first (failed)
  1.2.0 attempt that had *already* bumped VERSION. Nothing looked
  different, so the release step silently skipped. Fixed by changing
  the check's whole question from "did VERSION change in this push"
  to "does a release for the current VERSION already exist"
  (`gh release view "v$version"`) — idempotent regardless of how many
  pushes or failed runs it takes to land a version bump, and it can
  no longer be silently starved by a fix-forward commit whose own
  `before` already carried the bump.

**Nutrition & calorie tracking (`apps/nutrition`) — a whole new
subsystem** (`docs/NUTRITION.md`): BMR/TDEE estimation (Mifflin-St
Jeor, chosen over Harris-Benedict/Katch-McArdle — see the module's own
docstring), a suggested-not-self-reported activity level, goal-based
calorie/macro targets with two independent safety clamps (a rate cap
as a fraction of bodyweight, an absolute calorie floor), a food
diary, recipes, a diet-plan builder, and a weight-trend-based dynamic
calorie-adjustment engine — all following the same "explainable,
never a black box, user always has final control" shape already
established by `apps.progression`. Historized exactly like
`apps.records.PersonalRecord`: `NutritionGoal`/`NutritionTarget` rows
are never mutated in place, only superseded (`ended_at` stamped, a new
row created), enforced at the DB level via a partial unique
constraint on "at most one open row per user". Built in 8 phased
commits (models → energy engine → macros → weight-trend engine →
service layer → onboarding/OpenFoodFacts/dashboard → food diary →
recipes → diet builder → dashboard/nav), each with its own app-scoped
test run, full details of every phase's design decisions in
`docs/NUTRITION.md` itself rather than repeated here.

- **OpenFoodFacts integration, on-demand only.** Asked mid-build
  whether to bulk-import OFF's dataset (their 14-day-updating
  mongodb/JSON dump) or fetch on demand — flagged this specifically
  for a product decision rather than assuming, since bulk import has
  real self-hosted infrastructure cost (disk/bandwidth/CPU for a
  multi-GB dataset) that a fetch-on-demand design doesn't. Chosen: no
  bulk import, fetch only when a user searches (`apps/nutrition/
  openfoodfacts.py`), staleness-triggered refetch after 14 days, an
  admin-controlled kill switch (`OpenFoodFactsSettings`) to disable
  outbound requests entirely. A real (non-mocked) live call to OFF's
  API returned 403 until a `User-Agent` header was added — their API
  rejects requests with none.
- **A real, if narrow, security-shaped bug found via testing, not
  review**: every plain function-based view added across the whole
  feature (12 of them — `diary_entry_edit/delete`, `recipe_create/
  update/delete/ingredient_create/ingredient_delete/log`, `diet_plan_
  delete/item_edit/log`, `accept_adjustment_suggestion`) had no
  `@login_required`. Surfaced by a genuine test failure — `Anonymous
  User` isn't a real Django `Model` instance, so `.filter(user=
  request.user)` with an anonymous user can't be resolved by the
  ORM's FK-prep code and raises `TypeError: Field 'id' expected a
  number but got AnonymousUser` instead of gracefully matching
  nothing. Fixed for all 12. While tracking this down, confirmed via
  a live `curl` against the running container that the exact same bug
  independently pre-exists in `apps.measurements` and `apps.programs`'
  own equivalent function views (unrelated to this feature, left
  unfixed as out of scope, flagged here for whoever picks it up).
- Every `on_delete=PROTECT` on a user-owned line item referencing
  another of that same user's rows (`NutritionTarget.goal`,
  `RecipeIngredient.food`, `DiaryEntry.meal_slot/food/recipe`,
  `DietPlanMeal.meal_slot`, `DietPlanItem.food/recipe`) turned out to
  break whole-user deletion — Django's deletion collector checks
  `PROTECT` before it resolves whether the referencing row will also
  be deleted in the same operation, so deleting a user with any
  nutrition data referencing their own foods/recipes/meal-slots raised
  `ProtectedError`. Switched all of them to `CASCADE`, caught via a
  direct `User.objects.filter(...).delete()` in test cleanup, not
  code review.
- 165 new UI strings translated across fi/sv/ru/it/et (790 total at
  that point, 0 fuzzy/untranslated) for the whole feature: onboarding
  wizard, food diary, recipes, diet builder, dashboard.
- 165 tests, all passing, plus the full cross-app suite (826 tests)
  as the final safety net for the whole feature. Live-verified the
  complete flow over `curl` with a throwaway Finnish-language account:
  signup → nutrition onboarding (body → activity → suggested activity
  level → goal → review, real computed values at every step, e.g.
  "Tavoite: -0.5 kg/viikko → 1856 kcal/vrk.") → dashboard → food
  diary → foods → recipes → diet plans, every page rendering correctly
  in Finnish.

**A bug-hunting and calculators follow-up round**, asked for directly:
"jatka kalori ja ruoka ominaisuuden kehittämistä ja korjaa bugit tai
virheet mitä löydät esim UI:sta jne. lisää käyttäjien käyttöön myös
erilaisia hyödyllisiä laskureita" (continue developing the calorie/
food feature, fix any bugs found, add useful calculators). A live
walkthrough of every CRUD flow (food/recipe/diet-plan create, edit,
delete, log) via the Django test client surfaced three real,
independent bugs:

- `DiaryAddEntryView.post`'s OFF-import-failure error message
  ("That food couldn't be imported — try again.") was a bare Python
  string, never passed through `gettext` — `apps/nutrition/views.py`
  had no translation import at all despite every other string in the
  app going through one. Fixed; the string is now translated like
  everything else.
- `diet_plan_log` silently redirected back to the plan's own detail
  page on an invalid form (e.g. a bad date), giving the user zero
  indication anything had failed — inconsistent with `recipe_log`'s
  own established pattern of re-rendering the page with the form
  errors visible. Fixed to match; the shared meals-with-nutrition
  query (previously duplicated between `DietPlanDetailView.get` and
  this new error path) was factored into one helper,
  `_diet_plan_meals_with_nutrition`, picking up a `Prefetch` with
  `select_related("food", "recipe")` on the way that the original
  `prefetch_related("items")` was missing (an N+1 on every item's
  food/recipe lookup).
- `food_list.html` had a dangling, always-empty third table column
  (`<th></th>`/`<td></td>`) — dead markup from an action column that
  was never implemented. Removed.

**Four new standalone calculators** (`/nutrition/calculators/`,
`apps/nutrition/calculators.py` + `_CalculatorView` subclasses in
`views.py`): BMR/TDEE and macro split are thin forms in front of the
*existing* `energy.py`/`macros.py` functions, not a second
implementation of the same math; body fat % (U.S. Navy tape-measure
method) and daily water intake are two small new pure functions,
same no-DB/no-HTTP shape. Deliberately independent of the rest of the
app — no `NutritionProfile`, goal, or target is read or written by
any of them, so a user gets an answer without onboarding, without
setting a goal, and without logging anything, which is the whole
point. Each view is a plain `GET` with a query string (bookmarkable,
same convention as `FoodSearchResultsView`'s live search), pre-filling
from a signed-in user's own profile/goal/target where one exists but
leaving every field editable. Reachable from a card on the nutrition
dashboard and — since a user who hasn't onboarded can't reach the
dashboard at all — a direct link on the onboarding wizard's own first
step, so they're not a dead end behind a redirect. 24 new tests
(pure-function monotonicity/edge-case tests for body fat %, exact
Decimal-arithmetic tests for water intake, view-level tests checking
each calculator's output matches calling `energy`/`macros` directly,
unit-conversion round-trips, login-required regressions), 32 new UI
strings translated across fi/sv/ru/it/et (822 total, 0
fuzzy/untranslated). Live-verified in Finnish with real numbers
end to end: a 30-year-old 80kg/180cm moderately-active male's BMR/TDEE
calculator returned exactly 1780/2759 kcal (hand-verified against the
Mifflin-St Jeor formula), the macro calculator's 2100kcal fat-loss
split returned exactly 176.00g protein / 217.75g carbohydrate /
58.33g fat, the body fat calculator returned 19.8% for a 38cm neck/
90cm waist/180cm-tall man, and the water calculator returned exactly
3.1 L/day for an 80kg moderately-active user (2640ml base + 500ml
activity bonus). `ruff check .`, `makemigrations --check --dry-run`,
and the full 826-plus-new-tests cross-app suite all pass.

**The anonymous-access `login_required` bug flagged as pre-existing
in the previous entry turned out to be much wider than first
reported** — asked directly to fix it if anything needed fixing, a
full AST sweep of every `apps/*/views.py` for a plain function view
missing `@login_required` (the class-based views were never at risk;
`LoginRequiredMixin` is easy to forget to add once but impossible to
half-apply) turned up 21 more affected views across four more apps,
not just the two originally spotted:

- `apps.workouts` (9 views — the core training-log surface itself):
  `session_start`, `session_start_freeform`, `session_complete`,
  `session_abandon`, `session_delete`, `performed_exercise_add`,
  `set_log`, `set_edit`, `set_delete`. (`session_train`/
  `train_set_log` already had the decorator.)
- `apps.activities` (4): `activity_log`, `activity_edit`,
  `activity_delete`, `activity_type_deactivate`.
- `apps.exercises` (1): `exercise_deactivate`.
- `apps.measurements` (4) and `apps.programs` (7) — the two flagged
  last time — now actually fixed rather than just documented.

`apps.core.views.healthcheck`/`service_worker`/`web_manifest` are
deliberately excluded — a health-check endpoint and a PWA manifest/
service-worker script have to be reachable without a session by
design, not an oversight. One regression test added per app (matching
`apps.nutrition`'s own `test_the_plain_function_views_also_require_
login`), asserting each fixed URL redirects (302) for a logged-out
client instead of crashing. All 192 tests across the five touched
apps pass.

**A mobile layout bug in `apps.nutrition`**: some button rows ran
past the right edge of the screen — reported directly, alongside a
request to check the whole nutrition UI's mobile fit given how much
longer some translated labels run than their English source (German/
Finnish-style compound words in particular can be one long
unbreakable "word", and Russian labels routinely run 30-50% longer
than English). Root cause: `.card-action-row` (the shared
name-and-description-on-the-left, button-on-the-right pattern used
throughout the app) had no `flex-wrap`, and flexbox's default
`min-width: auto` refuses to let a text item shrink below its
longest unbreakable run — so a long label had nowhere to go but push
the button off the card. Fixed centrally in `static/css/base.css`
(`flex-wrap: wrap` on `.card-action-row` itself, `min-width: 0` on
its first child so the text can actually shrink/wrap first), which
benefits every other page using the same class
(`accounts/profile.html`, `core/feedback_list.html`, ...), not just
nutrition. Four nutrition templates with their own one-off inline
`display:flex` button/nav rows (the dashboard's "Recipes/Diet plans"
button pair, the food-search result rows' quantity input + submit
button, the diary day's previous/next-day nav and its per-entry edit/
delete buttons) got the same `flex-wrap:wrap` added directly, since
they don't use the shared class. Live-verified: `collectstatic` +
re-fetching `static/css/base.css` over `curl` confirmed the new rule
is actually served, and a logged-in Finnish session's rendered HTML
for the diary page shows the new inline `flex-wrap:wrap` in place.

**A genuinely unrelated test failure surfaced by chance while chasing
the full suite down**: `apps.analytics.tests.WeeklyVolumeSeriesTests.
test_sets_are_grouped_into_iso_weeks` failed with `3 != 2` the moment
the real-world calendar date crossed into a Monday during this same
session. The test logged sessions at `days_ago=0` and `days_ago=1`,
assuming both always land in the same ISO week — true on six days out
of seven, false specifically when "today" is a Monday, since ISO
weeks start on Monday and "yesterday" is then already last week.
Fixed by capping the second offset at `min(1, today.weekday())`
(0 on a Monday, 1 every other day) so the two sessions are always
provably in the same ISO week regardless of which real day the suite
happens to run on, rather than hand-picking a fixed offset and hoping.
Verified directly on a real Monday (today), where it now passes.

**Recipes stopped being "just text" — ingredients now pull their
macros automatically, and diet-plan meals can hold more than one
item.** Asked directly: "siitä pitäisi saada automaattisesti kaikkien
ainesosien makrot yms, nyt se on vain tekstinä" (it should
automatically get all the ingredients' macros, right now it's just
text). Turned out `docs/NUTRITION.md` had already documented the
intended design — "the food-search flow (diary/recipe 'add food')" —
but the actual `recipe_ingredient_create` implementation had drifted
from it: a bare `<select>` dropdown of `Food` rows the user already
had to create by hand elsewhere, no search, no OpenFoodFacts. Fixed
by giving it the exact same search-and-pick UX the food diary already
has:

- `FoodSearchResultsView`/`_food_search_results.html` generalized
  with a `mode` parameter (`diary`/`recipe`/`diet-plan-meal`) — one
  search implementation and one results partial serve all three
  call sites, branching only on which endpoint each result's "Add"
  button posts to.
- `RecipeIngredientSearchForm` replaces the old `RecipeIngredientForm`
  ModelForm/dropdown; `recipe_ingredient_create` now resolves a
  `food_id` or imports an `off_barcode` exactly like
  `DiaryAddEntryView` does, rather than a second copy of that logic.
- **Meal planning got the same treatment, plus a real capability
  gap closed**: a diet-plan meal was locked to the single item
  `diet_builder` originally generated for it — `diet_plan_item_edit`
  could only *swap* that one item, never add a second. There's no
  DB constraint stopping a meal from holding more than one
  `DietPlanItem`; it was purely a missing view/template. Added
  `diet_plan_meal_item_add` (same search-and-pick form, scoped to a
  meal instead of a recipe) and `diet_plan_item_delete`, and the
  detail page now shows each meal's actual running total ("target"
  vs. "so far") alongside its target, meaningful now that a meal can
  hold more than one line.
- **Barcode search, asked for immediately after**: "lisää
  ruoka-aine hakuun mahdollisuus hakea viivakoodin numeroilla" (add
  the ability to search by barcode numbers). A query that's nothing
  but 8-14 digits (`apps.nutrition.services._BARCODE_RE` — covers
  every format OFF itself indexes: EAN-8/UPC-A/EAN-13/ITF-14) is now
  matched exactly rather than run through free-text search: locally
  against `Food.off_id`, and against OpenFoodFacts' own by-barcode
  endpoint (`get_product`, the same one `import_or_refresh_food_
  from_off` already used) instead of its free-text search, which is
  unreliable for a bare digit string. Applies everywhere the new
  shared search partial is used — diary, recipe ingredients, and
  diet-plan meal items alike, one implementation. The search box's
  placeholder text now mentions it.

9 new tests (198 total for `apps.nutrition`), 6 new UI strings
translated across fi/sv/ru/it/et (826 total, 0 fuzzy/untranslated).
Live-verified end to end against the real (non-mocked) OpenFoodFacts
API in a Finnish session: created a recipe, searched "kana" and added
a locally-created "Kanafilee" ingredient by name (150g → exactly 248
kcal, matching 165 kcal/100g scaled), then searched a real barcode
(3017620422003, Nutella) and imported+added it directly (30g → 162
kcal, matching OFF's own 539 kcal/100g), watching the recipe's total/
per-serving nutrition update automatically both times with no manual
entry. Separately built a diet plan and added a second, manually
chosen item to a meal alongside `diet_builder`'s own auto-generated
one, confirming both persist independently and the meal's "target"/
"so far" totals render correctly in Finnish.

**The bottom-nav's Nutrition icon looked partially cut off.** Every
coordinate in the old hand-drawn fork/knife path checked out
mathematically within its `0 0 24 24` viewBox (nothing exceeded the
boundary, so it wasn't literally clipped by SVG overflow rules) —
but rendered at the real `.nav-icon` size (1.9rem) next to the other
five icons, screenshotted at a realistic 320px-wide viewport via
headless Chrome (no way to spot this from reading the path data
alone), it was visibly smaller and less filled-out than its
neighbors: a bare two-line "arch" for the fork rather than distinct
tines, and a curvy, ambiguous knife shape sitting further from the
viewBox edges than every other icon's own artwork does. Replaced
with Lucide's own "utensils" icon (a proper 3-tine fork + a
recognizable knife, both filling their `0 0 24 24` box the same way
the other five icons do) — same license-compatible outline-icon
family this project's other five nav icons were already drawn from,
not a one-off custom shape. Re-screenshotted at 320px afterward to
confirm — fills its box cleanly now, no ambiguity. Live-verified the
new path renders in the actual running app.

**A dedicated "Import from OpenFoodFacts" page, category browsing,
and a Nutri-Score/NOVA healthiness scale**, asked for directly.
Previously OFF import only ever happened inline while adding
something to a diary/recipe/plan — no way to just browse and grow the
shared food library on its own. Added:

- `FoodBrowseView` (`/nutrition/foods/browse/`) — a search box (a new
  fourth `mode="browse"` on the already-shared `FoodSearchResultsView`/
  `_food_search_results.html`) plus a "browse by category" section,
  and `food_import` (a plain POST, no quantity — this adds to the
  library, it doesn't log anything) as the one import endpoint both
  the search box and category pages post to. `next` is validated with
  `url_has_allowed_host_and_scheme` before ever being used as a
  redirect target — a POST field a user's own browser controls, but a
  same-site-only allowlist costs nothing and closes an open-redirect
  class of bug before it can exist.
- Category browsing uses OFF's own `/categories.json` (ranked,
  English-named categories, capped to a curated top N — see
  `docs/NUTRITION.md`) and `/category/<id>.json`. **Live-verified
  against the real API that this specific pair of endpoints is
  currently returning `503` from OFF itself** ("Page temporarily
  unavailable", no rate-limit headers — confirmed not caused by this
  session's own request volume, since `get_product`/`search_products`
  against the *same* OFF host succeeded seconds apart) — an
  OFF-side condition, not a bug here. `list_categories`/`browse_
  category` already return `[]` on any failure by design (same
  "browsing degrades gracefully" reasoning as the rest of the OFF
  integration), so the category section simply doesn't render rather
  than erroring — confirmed live. Nothing to fix; will recover on its
  own once OFF's own service does, since the code path is already
  correct for that case.
- **Nutri-Score (A-E) / NOVA (1-4)** — real, independently-published
  scales (`NutriScoreGrade`/`NovaGroup`, `docs/NUTRITION.md` "Food"),
  populated only from OFF's own `nutriscore_grade`/`nova_group` on
  import, never computed by this app for a hand-entered food. Shown
  as a small colored badge wherever a food's identity is listed
  (`_nutri_score_badge.html`) — food list, browse search results, and
  category listings.

18 new tests (216 total for `apps.nutrition`; 2 of the first pass's
own new tests had bugs of their own — one mocked a bare `Exception`
where the code specifically catches `requests.RequestException`, one
had a wrong expected list — both fixed, not the app code), 18 new UI
strings translated across fi/sv/ru/it/et (844 total, 0 fuzzy/
untranslated). Live-verified in Finnish against the real OpenFoodFacts
API end to end: searched "nutella" on the browse page, saw an
already-imported product correctly marked "Jo omissa ruoissasi"
(already in your library) alongside a *different* real product
showing a genuine Nutri-Score E / NOVA 4 badge pulled live from OFF,
imported it, and confirmed the same badge then appears on the Foods
list page.

**A general UI/UX pass over the whole nutrition section**, asked for
directly ("as easy to use and intuitive as possible") rather than any
specific bug. Deliberately did *not* introduce a new navigation
pattern for this — every other multi-page section of the app
(`apps.programs`'s Program → Workout → Prescription hierarchy,
`apps.measurements`, `apps.workouts`) already uses plain "&larr; Back
to X" links with no persistent sub-nav bar, so a tab strip just for
nutrition would have made the section *less* consistent with the rest
of the app, not more intuitive. Instead:

- The dashboard's link cards were split oddly across three different
  cards ("Recipes & diet plans", "Calculators", plus a single
  "Open food diary" button buried inside the totals card) with no
  direct link to Foods at all. Replaced with one "Quick links" card
  covering all five sections (Food diary, Foods, Recipes, Diet plans,
  Calculators) — same `.button-secondary`/flex-wrap row pattern
  already used everywhere, not a new component. Also added a
  "+ Log food now" shortcut next to "Open food diary" — `DiaryAddEntryView`
  already defaults to today with no `date` query param, so this reaches
  the add-food search box in one tap instead of two.
- The food diary's Previous/Next day buttons had no way to jump
  straight to an arbitrary date — added a `<input type="date">`
  between them (Alpine `@change` navigating via a `{% url %}`-built
  template with the date substituted in, not a hardcoded URL prefix,
  so it stays correct if the URL config ever changes), matching this
  codebase's established `x-data="{}" @event="..."` convention for
  small one-off interactions rather than introducing a new JS pattern.
- **A real functional gap, not just polish**: logging a recipe to the
  diary was hardcoded to *today* with no way to log one eaten
  yesterday or planned for tomorrow — inconsistent with diet plans,
  which already had a `date` field on `LogDietPlanForm`. Added the
  same field to `LogRecipeForm` (defaulting to today, editable), and
  the button label changed from "Log today" (no longer accurate) to
  "Log it" (matching the diet plan's own button).
- **Camera barcode scanning**, asked for directly right after the
  existing type-the-digits barcode search. Deliberately zero new
  dependencies: `static/js/barcode-scanner.js` uses the browser's
  native `BarcodeDetector` API to do the actual decoding, not a
  vendored JS library (CLAUDE.md "avoid unnecessary dependencies" —
  and this needs nothing to vendor at all, unlike this project's
  existing local-only `htmx.min.js`/`alpine.min.js`, since there's no
  library here to vendor in the first place). `supported` feature-
  detects `"BarcodeDetector" in window` and hides the "Scan barcode"
  button entirely on a browser that can't decode anything (Chromium/
  Chrome-for-Android — this app's primary mobile target per CLAUDE.md's
  "mobile-first" goal — supports it; Firefox and older Safari don't)
  rather than opening onto a broken camera view. One reusable include
  (`_barcode_scan_button.html`) plus the same `.modal-backdrop`/
  `.modal-card` overlay pattern `accounts/profile.html`'s changelog
  modal already established (`role="dialog"`, `@click.self="close()"`,
  `@keydown.escape.window="close()"`, matching transitions) — wired
  into all four food-search boxes (diary, recipe ingredients,
  diet-plan meal items, food browse) via the same shared
  `ironstackBarcodeScanner()` component. On a successful scan, the
  decoded barcode is written into the search `<input>` and a
  synthetic `keyup` event is dispatched (`.value =` alone fires no
  DOM event at all) so the existing `hx-trigger="keyup changed
  delay:400ms"` search box picks it up exactly as if it had been
  typed — no server-side change needed, since barcode-shaped queries
  were already handled specially by `apps.nutrition.services.
  _BARCODE_RE` (added earlier today).

7 new UI strings translated across fi/sv/ru/it/et (847 total, 0
fuzzy/untranslated). The camera scanner itself can only be
live-verified on real camera hardware in a real (HTTPS-serving,
Chromium-based mobile) browser — outside what a `curl`-driven session
can exercise — so verification here was: the feature-detection guard
tested directly in a browser console (`"BarcodeDetector" in window`),
every other page (button/script-tag presence, modal markup, dialog
semantics) confirmed via the test suite and a rendered-HTML check,
and the actual decode path (`detector.detect(video)` → `keyup`
dispatch → existing HTMX search) reviewed against MDN's documented
`BarcodeDetector` contract line by line rather than assumed.

**A real, live-only-reproducible bug found by chance while
live-verifying the recipe-logging date field in Finnish**: every
`type="date"` widget in `apps/nutrition/forms.py` (`LogRecipeForm.
date`, `LogDietPlanForm.date`, `BodyStepForm.birth_date`) rendered
its value in the *active locale's* date format ("17.08.2026" in
Finnish) instead of the ISO 8601 format
(`YYYY-MM-DD`) an HTML5 `<input type="date">` requires for its
`value` attribute — a browser silently rejects any other format and
shows the picker empty rather than pre-filled with today's date. If a
user then submitted without touching the (apparently already correct,
actually blank) date field, the browser would send an empty string
and the form would fail its own "this field is required" validation —
a real, not just cosmetic, breakage for exactly the "just hit log"
flow this session's own `LogRecipeForm`/`LogDietPlanForm` date
fields exist to make easy. Fixed with `format="%Y-%m-%d"` pinned on
each widget, exactly the fix `apps.activities.forms`'s own `date`
widget already had — this bug's fix already existed as a precedent
elsewhere in the codebase, apps.nutrition's own widgets just hadn't
followed it. Invisible with English active (Django's default locale
format happens to already look ISO-ish there), which is presumably
why none of this session's many earlier English-context checks caught
it. 3 new regression tests, each explicitly activating Finnish
(`django.utils.translation.override("fi")`) and asserting the
rendered `value` attribute stays ISO. Live-verified: both
`LogRecipeForm` and `LogDietPlanForm` now render `value="2026-08-17"`
in a live Finnish session, confirmed via `curl` before and after the
fix.

**Two admin-only Django-admin bulk actions on `Food`, both asked for
directly.** "Merge selected foods into one…" — the shared library
inevitably accumulates near-duplicates, and deleting one outright
would `CASCADE`-delete every `DiaryEntry`/`RecipeIngredient`/
`DietPlanItem` that ever referenced it, exactly the kind of silent
history loss CLAUDE.md's "workout history must remain historically
trustworthy" warns against. `apps.nutrition.services.merge_foods`
re-points every such reference onto a kept row instead (an
intermediate confirmation page — new custom admin URL/view/template,
`templates/admin/nutrition/food/merge.html`, the project's first —
lists every selected food so a human picks which one, never a
heuristic), then deletes the now-unreferenced duplicates. "Refresh
selected foods from OpenFoodFacts" — `import_or_refresh_food_from_
off` gained a `force=True` parameter bypassing the normal 14-day
staleness gate, used only by this action; still an explicit,
admin-chosen selection, not the unconditional bulk re-sync this
integration was deliberately scoped away from at the start.

- **A real bug in my own translation tooling, not the app**: the
  merge confirmation page's explanatory paragraph was written as a
  multi-line `{% blocktrans %}` in the template, and the scratchpad
  `gen_po.py` script that regenerates every locale's `.po` file
  writes each `msgid`/`msgstr` as one raw quoted line with no
  handling for an embedded literal newline — corrupting not just that
  one entry but, since the script also (over)writes the `en` catalog
  it reads its own extraction list from, the whole build's source-of-
  truth file, breaking `compilemessages` for every language with a
  `syntax error`. Fixed at the source rather than in the tooling
  (lower-risk under time pressure than debugging PO multi-line
  string-continuation escaping): rewrote the template's blocktrans as
  a single line, matching the convention every other translated
  string in this project already used — regenerated `locale/en` and
  every other locale from scratch (`makemessages` on a clean slate)
  to clear the corruption, confirmed `msgfmt --check` passes with 0
  errors for all six languages.
- 15 new UI strings translated across fi/sv/ru/it/et (862 total, 0
  fuzzy/untranslated).
- Live-verified end to end as a real superuser in Finnish: created
  two near-duplicate "Kananrinta" foods, selected both, ran "Yhdistä
  valitut ruoat…", confirmed the intermediate page listed both and
  the merge left exactly one behind (the other's pk confirmed gone
  from the database directly). Separately, reset a real previously-
  imported food's `off_synced_at` to `None` and its name to a stale
  placeholder, ran "Päivitä valitut ruoat OpenFoodFactsista" against
  the real (non-mocked) OpenFoodFacts API, and confirmed the name and
  `off_synced_at` both came back correct — a genuine forced refresh,
  not a no-op.

## Nutrition development plan and its first three follow-ups

Asked directly for a development plan for `apps.nutrition`, to be
implemented, not just written. Re-read `docs/NUTRITION.md`'s own
"Phased implementation plan" first — phases 1-9 were all genuinely
done, but two real gaps turned up: `apps.api` never got nutrition
viewsets despite phase 10 asking for them (documented honestly as a
known follow-up rather than silently dropped), and there was no way
to see nutrition trends over more than one day at a time, no way to
repeat a previous day's logging, and no way to quickly re-log a food
you eat often — all real day-to-day friction that only becomes
visible once an app has actual usage history to design against, not
gaps the original spec could have named up front. Picked the three
highest-value items against CLAUDE.md's own stated goals ("provide
extensive statistics and charts", "training log first") and the
`apps.api` gap as a separate documented follow-up, not built this
round (CLAUDE.md: "do not attempt to build the entire application in
one pass").

**Nutrition statistics page** (`/nutrition/stats/`,
`NutritionStatsView`) — a 30-day daily-calorie bar chart plus average
calories/macros compared against the current target, reusing
`apps.core.charts.build_bar_series`/`templates/core/_bar_chart.html`
verbatim (the same component `apps.analytics`'s own stats page uses,
not a second chart implementation). `calorie_history` always returns
one point per day, including unlogged days as zero — the same
"never skip a day" rule `apps.analytics.services.weekly_volume_series`
already follows, so a genuinely quiet day shows as a real dip rather
than compressing the timeline. `nutrition_stats`' averages count only
days something was actually logged, deliberately: counting a day
nobody logged anything as a zero-calorie day would understate the
real average for anyone who logs most days but not literally every
one, which is most real usage.

**"Log again" quick re-log** (`services.recent_diary_foods`,
wired into `diary_add_entry.html` above the search box) — a
one-tap repeat of a user's most recently logged foods, each keeping
the exact meal slot and quantity it was logged with last time.
Deliberately derived live from `DiaryEntry` history rather than a new
`FavoriteFood` model — the same "derive, don't store a duplicate"
rule the rest of this app already follows for daily totals, so there
was no real case for a new table just to rank recency/frequency.

**Copy a diary day** (`services.copy_diary_day`, a collapsible form
on `diary_day.html`) — "I ate the same as yesterday" without
re-adding every item by hand. Duplicates every entry from one date
onto another as brand-new rows; the source day is never read back out
or mutated, and copying twice (or onto a day that already has
entries) just adds more rows rather than silently deduplicating — the
same "an explicit action does exactly what it says, nothing silently
different" reasoning `merge_foods` already established, and the same
CLAUDE.md "history must remain historically trustworthy" principle
extended to nutrition data the rest of this app already leans on.

18 new strings translated across fi/sv/ru/it/et (880 total messages,
0 fuzzy/untranslated across all six locales including a genuine
plural form for "Copied N entry/entries to <date>"). 262/262
`apps.nutrition` tests pass; `ruff check` clean. Live-verified end to
end as a real user in a Finnish session: the stats page rendered
"Ravinnon tilastot" with a correct 250 kcal average (200 + 300 kcal
over 2 logged days out of 30) and the right per-day chart tooltips;
posting "Kopioi tämä päivä toiselle päivälle" duplicated a real entry
onto a date three days out; tapping a "Kirjaa uudelleen" quick-add
card created a new entry with the same food and quantity on a
different date — all three confirmed by querying the database
directly afterward, not just checking for a 200/302 response.

## Nutrition navigation fix and recipe improvements

Reported directly: "quick links, painan jotain ja siirryn sinne
sivulle, sieltä sivulta ei pääse enää takaisin edelliseen näkymään"
(pressing a Quick link and being unable to get back). Root cause: the
Foods/Recipes/Diet plans list pages' "back" link was written back
when those pages were only ever reached from the food diary, and
still pointed there — reasonable at the time, actively wrong once the
dashboard's own "Quick links" card started linking to them directly
too. Arriving via Quick links and pressing "back" landed on the food
diary, not the dashboard the user had actually come from. Fixed by
pointing all three at the nutrition dashboard (their real common
parent now that they're reachable two different ways), and gave the
food diary itself — also directly reachable from the dashboard, not
just bottom-nav — the same "&larr; Back to nutrition" link it never
had. 4 new regression tests lock in the fix. Documented the general
rule in `docs/NUTRITION.md`: a "back" link's target is a page's
logical parent, not wherever it happened to be built to be reached
from first — those drift apart the moment a second entry point is
added later.

(A follow-up ask — making every link's return target track the
*exact* page a user actually came from, "id html tagin avulla" —
is still unclear on what "id" was meant to refer to; paused pending
clarification rather than guessing at a systematic rework across
every template.)

**Recipe creation and content, developed further** on direct request.
Real gap found: a recipe ingredient's quantity could only be changed
by deleting it and re-adding it through the whole search-and-pick
flow again, losing its position in the list (`order`) in the process.
`RecipeIngredientQuantityForm` + `recipe_ingredient_edit` fixes that
with a quantity-only edit, same shape as the food diary's own
`DiaryEntryQuantityForm`/`diary_entry_edit`. The recipe list
(`RecipeListView`) gained a `?q=` name search (same
`name__icontains` pattern `FoodListView` already uses) and now shows
each recipe's calories per serving directly, instead of making every
recipe a guess until opened. The recipe detail page's nutrition table
now shows fiber, sugar, saturated fat, and sodium whenever at least
one ingredient carries that data — fields `Food` has always stored
(mostly populated by OpenFoodFacts imports) but that, until now,
nothing in the whole nutrition UI ever surfaced anywhere. Creating a
recipe now shows a one-time "created — now add its ingredients below"
message, since the create-then-add-ingredients two-step flow wasn't
obviously connected otherwise.

9 new strings translated across fi/sv/ru/it/et (889 total messages, 0
fuzzy/untranslated). 274/274 `apps.nutrition` tests pass (8 new: nav
regression tests + ingredient-edit/search/nutrition-fields
coverage); `ruff check` clean; `makemigrations --check` clean (no
schema changes — everything here reuses existing fields). Live-
verified end to end in a real Finnish session: built a two-ingredient
recipe (one ingredient carrying fiber/sodium, the other not), and
confirmed the detail page showed exactly "Kuitu"/"Natrium" and
correctly hid "Sokeri"/"Tyydyttynyt rasva" (neither ingredient had
that data — the per-field conditional, not an all-or-nothing table);
edited an ingredient's quantity from 300g to 250g and confirmed via
the database that its `order` stayed untouched; searched the recipe
list by name and confirmed both a match and a "no results" case;
created a new recipe and confirmed the "luotu — lisää sen ainesosat
alla" toast rendered on the very next page.

## "Most used" quick add, generalized and cross-context

Asked directly: a quick-add panel showing the ~10 most-used
ingredients wherever a food can be added, so a food eaten often
doesn't need a fresh search every time. This generalizes and replaces
the diary-only, recency-ranked "Log again" panel from earlier this
session: `services.most_used_foods(user, limit=10)` ranks by
**frequency** (a `collections.Counter` over `DiaryEntry` +
`RecipeIngredient` + `DietPlanItem` usage combined, so "most used"
means overall, not per-context), and one shared, mode-parameterized
template partial (`_most_used_foods.html`, exactly
`_food_search_results.html`'s own established pattern) shows it in
all three add-a-food contexts — the food diary, a recipe's
ingredients, and a diet-plan meal's items — not just the diary. Each
entry still prefills a sensible quantity (and, where it exists, a
meal slot) from that food's most recent direct diary use, falling
back to the food's own serving size for a food only ever used via a
recipe or diet plan. `recent_diary_foods` and its "Log again" UI are
removed entirely rather than kept alongside the new panel — two
similar-but-different quick-add lists on the same page would have
been more confusing than either alone, and the new one is a strict
superset of what the old one covered (any food the old one would
have shown was, definitionally, used at least once).

**A second real, live-found bug, same family as the earlier
`type="date"` one**: the quantity field on every food-search "Add"
card — not just the new "Most used" panel, the *existing*
`_food_search_results.html` cards too — rendered its `value`
attribute with the active locale's decimal separator (a comma in
Finnish, `value="100,00"`), but HTML5 `type="number"` requires a
period regardless of locale; a browser silently rejects the malformed
value and leaves the field empty rather than pre-filled. Invisible
with English active, exactly like the date bug, and found the same
way: live-verifying the new feature in a Finnish session rather than
trusting it because the (English-only) automated tests passed. Fixed
with `{% load l10n %}` + the `|unlocalize` filter on the three
hand-written `type="number"` inputs in the app (`_food_search_
results.html` ×2, `_most_used_foods.html` ×1) — every other number
input in the app is Django-form-widget-rendered and already immune,
since `NumberInput.format_value` avoids localization on its own; only
these hand-written ones bypassed that protection. 2 new regression
tests (`NumberInputLocaleFormatTests`), same shape as
`DateInputWidgetLocaleFormatTests`.

1 new string translated across fi/sv/ru/it/et ("Most used" — net
message count unchanged at 889, since "Log again" left the catalog
the same moment "Most used" entered it). 280/280 `apps.nutrition`
tests pass; `ruff check` clean; `makemigrations --check` clean.
Live-verified end to end in a real Finnish session: built a usage
history where one food was used 3 times (2 diary entries + 1 recipe
ingredient) and another only once, confirmed "Eniten käytetyt" showed
the first food ranked above the second on the diary add-food page
*and* the recipe-ingredient-add page; confirmed the quantity field
read `value="180.00"` (period) after the l10n fix, not
`value="180,00"` (comma) as it did before.
