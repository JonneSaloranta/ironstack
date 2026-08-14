# UI Guidelines

## Core principle

Mobile-first.

Design initially for approximately 360–430px wide screens, then progressively enhance for desktop.

## Workout logging

This is the most important interaction in the application.

The user should be able to log:

```text
Exercise
Set
Weight
Reps
```

with as few taps as reasonably possible.

The interface must support:
- quick weight entry
- quick rep entry
- set completion
- editing
- RPE/RIR when desired
- failure marking
- notes
- suggested next weight

Do not make workout logging a multi-step form process.

## Dashboard

The dashboard should prioritize action and useful current information.

Possible content:

```text
Next workout
Last workout
This week's workouts
This week's volume
Recent PRs
Body weight
Recent activity
```

## Mobile navigation

A bottom navigation pattern is appropriate for mobile.

Possible sections:

- Home
- Workout
- Programs
- Progress
- Profile

Do not overload navigation with every feature.

## Desktop

Desktop can use:
- sidebar navigation
- wider analytics layouts
- tables
- larger charts
- multi-column cards

Keep information architecture consistent with mobile.

## Accessibility

Support:
- keyboard navigation
- visible focus states
- proper labels
- accessible forms
- sufficient contrast
- useful touch target sizes
- screen reader-friendly controls

Never rely on color alone to convey status.

## States

Major screens should handle:
- loading
- empty
- error
- success
- mobile
- desktop

## Visual style

The interface should feel like a practical training tool rather than a social media application.

Avoid unnecessary visual clutter, excessive animations, and tiny controls.

## Implementation

### Workout logging
Quick weight/reps entry, editing, RPE/RIR, failure marking, and notes are
all on the single set-log form (`templates/workouts/_performed_exercise_card.html`)
— no multi-step flow. "Suggested next weight" (Phase 7) pre-fills that
same form for a performed exercise's first set only, shown with its
confidence and reason as a plain, editable default, never forced (see
`docs/SMART_SUGGESTIONS.md`).

### Training mode
A second, deliberately narrower way to log the same sets — the full
session-detail page (above) shows every exercise in the workout at once
with its complete history and edit/delete controls, which is the right
tool for reviewing or correcting a workout but too much to actually hold
a phone and squint at between sets at the gym. Training mode
(`apps.workouts.views.session_train`/`train_set_log`,
`templates/workouts/session_train.html`/`_train_panel.html`) shows one
exercise at a time: its target, the suggested weight (same
`suggest_weight` engine and same "editable default, never forced" rule
as the full page), sets already logged this exercise, a compact log-set
form, and a rest timer — nothing else.

**Reaching it**: a floating round button (`.training-fab`, a dumbbell
icon with a small pulsing "live" dot) appears in the bottom-right corner
of *every* page, not just the workout ones, whenever the logged-in user
has a session in progress — the whole point is not having to navigate
back to the workout section first. This is a global context processor
(`apps.workouts.context_processors.active_workout_session`, registered
in `config/settings/base.py`), not something each view adds to its own
context, so it works from the dashboard, exercise library, anywhere.
Hidden for anonymous users and once the session is completed/abandoned.

**Current exercise**: automatically the first exercise (in program
order) that still has sets left to log against its target
(`apps.workouts.services.is_performed_exercise_complete`/
`first_incomplete_performed_exercise` — a prescribed exercise is
"complete" once it has its snapshotted `set_count`; a freeform/ad-hoc
addition, which has no target, counts as complete after one set, since
there's no way to know a user wants a second one until they say so).
Logging that exercise's last needed set auto-advances to the next
incomplete one. This is only ever the *default* — Prev/Next buttons
(`?pe=<id>`, also a plain link so it works with JS disabled) let a user
jump to any exercise in the workout regardless of completion state,
matching "the user always has final control over ... progression" (this
doc's parent, CLAUDE.md). Once every exercise is done, the last one
stays shown (with a "done" banner above it) rather than the page going
blank — logging a bonus set is still one tap away, and Complete/Abandon
buttons are always present, not gated on finishing everything.

**Rest timer**: pure client-side countdown (Alpine.js, `x-data`
component defined inline in `session_train.html`) — no server round
trip while it's running, no model field for it. Presets (60/90/120s)
plus ±15s adjustment and skip. Auto-starts after a successful log via
htmx's `HX-Trigger` response header (`train_set_log` sets
`rest-timer-start` only on an actual successful log — a generic
`htmx:afterRequest` listener can't distinguish that from a validation
error re-showing the same form, since both return HTTP 200). The timer
widget deliberately sits *outside* the HTMX swap target
(`#train-panel`) in the page's DOM — logging a set or navigating
Prev/Next swaps the exercise panel, and a countdown in progress would
reset to zero if it were nested inside that swapped region. When the
countdown reaches zero on its own it also plays a short two-tone chime
— synthesized with the Web Audio API (an oscillator, not an audio file,
so nothing needs shipping/loading for one beep) and wrapped in
try/catch for a browser that doesn't support Web Audio at all, in which
case the visible countdown reaching zero is all that happens. A speaker
icon next to the "Rest timer" label mutes/unmutes it; `muted` persists
in `localStorage` (a device setting, not a server-side preference — the
same reasoning as a browser's own volume control) so it survives across
page loads. A manual "Skip rest" never beeps — only the countdown
actually finishing does.

One `AudioContext` is created lazily, once, and reused for the page's
whole life — regression: the original version created a fresh one
inside `beep()` itself every time, which is silent on iOS Safari (and
other WebKit browsers) specifically, because they refuse to ever
produce sound from a context that wasn't created/resumed synchronously
*inside* a genuine user gesture, and `beep()` only ever runs from a
`setInterval` callback (the countdown reaching zero) or an `HX-Trigger`
event handled well after the "Log set" tap that caused it — neither
counts. Fixed by unlocking the context (creating it, then scheduling a
near-silent blip, which is what actually satisfies iOS specifically —
calling `resume()` alone isn't reliably enough there) on the very first
tap/touch anywhere on the page, which is guaranteed to happen before any
rest period could ever finish, since reaching training mode at all means
the user just tapped something.

**Progressive enhancement**: every training-mode interaction (Prev/Next,
logging a set) works as a plain link/form POST with JS disabled — HTMX
just upgrades it to a partial swap instead of a full page reload
(`hx-select="#train-panel"` on Prev/Next reuses `session_train`'s own
full-page response and extracts just the fragment, so there's no
separate "just the panel" endpoint to keep in sync). `train_set_log`
explicitly checks the `HX-Request` header and redirects to the full page
for a non-HTMX POST, rather than ever returning the bare `#train-panel`
fragment as if it were a whole document — that fragment has no
`<head>`/stylesheet/nav of its own.

Not built: a per-prescription custom rest duration (`ExercisePrescription`
has no such field — the three presets are a fixed UI convenience, not a
domain rule) and in-training set editing/deletion (corrections stay on
the full session-detail page, reachable via the "Full view" link — kept
out of training mode deliberately, to keep its one screen to logging
forward, not fixing mistakes).

### Dashboard
Implemented: this week's workouts and volume, recent PRs (last 3), body
weight, BMI (see below), an achievements carousel (see below), and an
in-progress-workout banner that doubles as "continue/last workout". No
logout button here — it lives on the Profile page only, not duplicated.

Opens with a varied, time-of-day-aware greeting (`apps.core.greetings`,
`.dashboard-greeting`, right below the "IronStack" heading) — mixes
encouragement and light humor, picked at random on every render from a
pool keyed to morning/afternoon/evening/night in the user's own active
timezone. Originally lived on the profile page in place of a static
"Signed in as X" card, then moved here: Home is the page a user
actually lands on, so that's where a greeting belongs.

**Achievements carousel** (`apps.analytics.achievements`,
`templates/core/dashboard.html`): a small `.card` at the top of the
dashboard, auto-rotating every 4.5s through all-time highlight cards —
longest streak (consecutive calendar days with a completed workout),
total workouts completed, total PRs, and total weight lifted — pausing
on hover/focus and always overridable via a dot row (auto-rotate is only
ever the default, never the only way to move between cards). Unlike
every other dashboard widget, this one is **shared across every user on
the instance**, not personal to whoever's looking at it — each card
names whose achievement it is (a self-hosted app is typically a
household/small-gym's own server, where seeing a housemate hit a new
streak is the point, not a bug). `User.show_achievements` is
consequently a *privacy* setting rather than a display one: turning it
off removes that user's own figures from what the carousel shows to
**everyone**, themselves included — not a personal "hide the carousel
from me" toggle the way `show_bmi` is for BMI. A user with zero
completed workouts contributes no cards; the carousel itself doesn't
render at all if nobody currently has anything to show.

Implementation notes: each achievement slide is absolutely positioned
inside a fixed-min-height box rather than left in normal flow —
regression: with only `x-show`'s display toggling, the *container's*
height followed whichever slide was currently visible, so the page
content below the carousel visibly jumped up and down every rotation as
it cycled between a one-line and a two-line achievement. Also fixed
alongside this: `.bottom-nav` had no `z-index` at all, so a card near
the bottom of a long page (the profile page's BMI card, specifically its
`<abbr>` tooltip) could end up painted *over* the nav bar instead of
under it — the nav now sits above ordinary page content and tooltips,
below the floating training button/PR toasts (deliberately still above
everything, nav included).

**"Recently active" list** (`apps.analytics.achievements.recently_active_users`,
same template): a plain list, one row per opted-in user who has ever
started a workout session, most recently active first, capped to 10
rows. Same sharing/privacy model as the achievements carousel above —
`User.show_achievements` governs both, so its label/help text now say
"activity" rather than narrowly "achievements". Counts *any* session
status, not just completed ones (starting a workout is itself a sign of
activity), which is what lets a still-in-progress session show as
**"Training now"** (an accent-colored pulsing dot, the same visual
language as the training FAB's own "still going" indicator) instead of
an ordinary elapsed-time reading. Otherwise each row shows
`{{ time }} ago` — the magnitude/unit part (`"2 hours"`, `"3 days"`)
comes from Django's own built-in `timesince` filter, which is already
translated into every locale this app ships as part of Django core, so
only the wrapping phrase itself needed a new translatable string. A
session within the last 24 hours also gets a plain green dot (no pulse)
as a secondary, at-a-glance freshness cue; older activity gets a plain
muted dot.

**BMI**: dashboard card once both a height (set on the Profile page) and
at least one logged body weight exist, alongside the WHO category ranges
table with the user's own row highlighted — a bare number with no
context isn't useful. A `show_bmi` profile toggle turns the card off
outright for anyone who'd rather not see it, independent of whether it's
computable. Three dashboard states, each with its own nudge card (all
respecting the toggle) rather than silently showing nothing: no height
yet ("add your height"), height set but no body weight logged yet ("log
a body weight"), and both present (the actual BMI card,
`templates/core/_bmi_card.html`).

The ranges table (plus the current value once computable) is *also*
shown unconditionally on the Profile page itself, right below the
`show_bmi` toggle — the dashboard card is reachable only through a chain
of "if this, if that" states, so a user who hadn't logged a body weight
yet had no way to find the scale at all; Profile is guaranteed reachable
from the main nav regardless of data state.

Two items from this doc's original wishlist were deliberately
not built as dashboard widgets:
- **Recent activity** — `apps.activities` already has its own working,
  dedicated history page per activity type; a dashboard widget would
  just be a second, staler copy of the same data.
- **Next workout** (a specific upcoming *scheduled* workout) — programs
  do carry an optional per-workout weekday (`Workout.scheduled_weekday`,
  Phase 3), but nothing reads it to compute "what's next" yet. The
  in-progress banner covers "what am I doing right now"; a
  schedule-aware "next workout" widget would be new logic, not just a
  new dashboard card, so it's left out rather than half-built.

### Mobile navigation
All five bottom-nav sections are real, not placeholders: Home
(dashboard), Workout (session history/start), Programs, Progress
(analytics dashboard — training volume, muscle-group breakdown, PR
history), Profile (unit/timezone/language preferences, account details,
password change — added Phase 11, previously a dead link since Phase 1).
Each nav item's active state (`aria-current="page"`) is keyed off
`request.resolver_match.view_name`, the namespaced form — Home and
Progress once both matched on the bare `url_name` "dashboard" (Django's
core dashboard and the analytics dashboard happen to share that name
within their own apps), lighting up both at once while viewing Progress.

"Progress" originally linked to Body tracking (measurements) — retargeted
to the analytics dashboard once a post-launch review found the label
didn't match where it led, and pointed out that the dashboard's own
"Analytics"/"Workout history"/"Programs" cards duplicated what the main
nav already reached. Those three cards were removed; Body tracking stays
reachable from its own dashboard card instead (it isn't in the bottom nav
at all now, alongside Browse exercises and Activities — none of the
three needed a dedicated nav slot).

### States
- **Loading**: `.htmx-request` (`static/css/base.css`) dims and disables
  the triggering element automatically during any HTMX request — htmx
  toggles the class itself, so no per-form wiring is needed anywhere in
  the app.
- **Empty**: `.empty-state` cards, used consistently everywhere a list or
  history can legitimately be empty.
- **Error**: form field errors render inline next to the field
  (`.field-error`) everywhere; custom `404.html`/`403.html` (extend
  `base.html`) and a deliberately standalone `500.html` (Django renders
  it with no context processors at all, so it can't depend on
  `{% url %}`/`{% static %}` — see `templates/500.html`'s own comment).
- **Success**: all feedback — the Django messages framework ("Preferences
  saved", etc.) and new-PR notices alike — renders as a toast fixed to
  the top of the screen, sharing one `#pr-toast-container` and the same
  `.pr-banner` markup/behavior (Alpine `x-show` + `setTimeout`, own close
  button, auto-dismissing after 6 seconds). The messages framework is
  rendered directly into that container in `base.html`; PR notices are
  the one path that also arrives via an HTMX out-of-band swap
  (`templates/records/_pr_toasts.html`; see "Workout logging" → training
  mode note below) for the no-JS-navigation case, since a plain full page
  load still needs its own PR toast rather than waiting on an HTMX swap
  that never runs on that request. Error-level messages
  (`message.tags == "error"`) get the `.pr-banner-error` variant instead
  of the default success styling. Nothing renders as a permanent static
  card anymore — every piece of transient feedback disappears on its
  own.

### Desktop
Charts (`docs/ANALYTICS.md`) scale up for free — `viewBox` + `width:
100%` means a chart already renders larger as the page's `.container`
widens past the `768px` breakpoint (`static/css/base.css`), no separate
desktop chart sizing needed.

The bottom nav becomes a horizontal top bar on desktop (`position:
sticky; top: 0`), not a sidebar — deliberately the same
`flex-direction: row` as mobile, just pinned to the top instead of the
bottom. An earlier sidebar version had a real bug worth remembering: CSS
Grid's default item alignment stretched the nav column to the full page
height, and the nav links' mobile `flex: 1` (correct there — equal-width
tabs in a narrow horizontal bar) then grew each link into an equal fifth
of that height once the direction switched to a column — hugely
oversized nav items. Keeping the same row direction at every width
sidesteps that failure mode entirely rather than patching around it.

### Mobile navigation
Order: Home, Progress, Workout, Programs, Profile. Icon-only on mobile
(inline SVGs, `.nav-icon`) — no room for both icon and label at
360–430px — with the label visually hidden (`.nav-label { display:
none }`) rather than removed: the accessible name comes from each
link's own `aria-label`, and desktop re-shows the label text alongside
the icon once there's room (`display: inline` past the `768px`
breakpoint).

Icons and the bar itself were enlarged post-launch (1.5rem → 1.9rem
icons, 3.5rem → 4.25rem bar height, plus explicit `padding-top` on each
link so icons aren't flush against the bar's top edge) — the original
sizing felt cramped specifically when the app is installed and run as a
standalone PWA, with no browser chrome nearby to lean on for scale.

### Accessibility
Beyond the base checklist above: a skip-to-content link
(`.skip-link`, visible on focus); every hand-rolled `<input>`/`<select>`
outside the standard `field.label_tag` form loop carries an explicit
`aria-label`; every chart carries a visible heading (not just an SVG
`aria-label`) and, for bar charts specifically, a real data table below
it — a categorical color legend wouldn't have helped since every bar in
a given chart is deliberately the same color (see
`templates/core/_bar_chart.html`'s own reasoning). This was an explicit
post-Phase-11 audit, not part of the original Phase 11 pass — two real
gaps were found and fixed; see `docs/ANALYTICS.md` "Chart titles/legends
audit".

### Deleting a logged workout
`WorkoutSession.delete` is reachable only from the session's own detail
page (with a confirm dialog), deliberately not from the `/workouts`
history list even though that list can show many sessions at once — a
destructive action stays one deliberate navigation away from an
accidental tap, rather than sitting on every row of a scrollable list.
Works regardless of status (in-progress, completed, or abandoned) —
distinct from `abandon`, which keeps the session in history marked
abandoned rather than removing it.

### Navigational buttons
Every "Back to X" link (program/exercise/workout/measurement/activity/
records pages, every create/edit form, and the error pages) is styled as
a button (`.button-secondary` — outlined, lower-emphasis than the solid
`.button` a page's primary action uses) rather than a bare text link:
easier to spot, and a full `--touch-target` (2.75rem) hit area instead of
whatever a line of text happens to occupy. The same rule now applies to
every other in-page action link too — Cancel, the row-level Edit links
in workout/measurement/activity history tables (compacted to
`--touch-target: auto` inside `.set-actions`, matching the row's own
Delete button), exercise-list pagination, "View current PRs", and
login/signup's counterpart link. Where the old link carried both prose
and a call to action in one sentence ("Already have an account? Log
in"), that's now split: plain muted text for the question, a short
`.button-secondary` for the action — so the label stays scannable
inside a button while the context is still right next to it. Two
patterns are deliberately exempt: the mobile bottom-nav (its own
icon-first `aria-current` styling, not a page-body link) and
`.range-filter`'s date-range tabs (a segmented control with its own
pill/active-fill styling, not a text link pretending to be one).

### Profile page
"Account details" (username/name/email — `apps.accounts.
AccountDetailsView`, kept separate from both the preferences form and
the password change flow), "Change password", "API keys", and (staff
only) "Admin" each render as a `.card-action-row`: descriptive text on
the left, a single explicit `.button-secondary` on the right as the
only actual link. Regression: these used to be one giant
`<a class="card card-link">` wrapping the whole card, with nothing
about a plain card visually signaling it was clickable at all.

### PWA
Installable, not offline-capable — see `docs/ROADMAP.md` "Future
possibilities" for exactly where that line is drawn and why. A web
manifest (`static/manifest.json`, served at `/manifest.json` — the site
root, not `/static/`, matters for the service worker's scope) declares
name/icons/`display: standalone`/theme color; a hand-drawn barbell icon
(`static/icons/icon.svg`, rasterized to the PNG sizes browsers actually
request) doubles as the app icon and favicon. The service worker
(`static/sw.js`) exists only to satisfy installability criteria and
stale-while-revalidate genuinely static assets (CSS/JS/icons) — it
explicitly never intercepts a page, form submission, or HTMX response,
so nothing here can ever show stale workout data or silently swallow a
logged set while offline.

Static assets were originally cached pure cache-first — regression: once
an asset was cached, it was served from that cache *forever*, since
these files aren't served at content-hashed URLs (no
`ManifestStaticFilesStorage`), so the network was never consulted again
for it. A whole session's worth of CSS fixes (chart bars going missing
being the one that actually got reported) was consequently invisible to
any returning visitor whose browser had cached `base.css` before those
fixes shipped — a real, unaddressed live-deploy caching bug, not a bug
in the CSS itself. Fixed with stale-while-revalidate instead: the cached
copy is still served immediately (fast, works offline), but every
request also refetches in the background (`event.waitUntil` keeps the
worker alive for it) and updates the cache for the *next* request, so a
deployed fix reaches every browser within one extra load rather than
never. `STATIC_CACHE`'s version string was also bumped once alongside
this fix specifically to force every existing installation to discard
its old cache immediately, rather than only self-healing gradually as
each asset happened to get re-requested.
