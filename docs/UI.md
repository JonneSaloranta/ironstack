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

### Dashboard
Implemented: this week's workouts and volume, recent PRs (last 3), body
weight, BMI (see below), and an in-progress-workout banner that doubles
as "continue/last workout". No logout button here — it lives on the
Profile page only, not duplicated.

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
history), Profile (unit/timezone preferences, password change — added
Phase 11, previously a dead link since Phase 1).

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
- **Success**: the Django messages framework (PR banners, "Preferences
  saved", etc.), rendered in `base.html`.

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
whatever a line of text happens to occupy.

### PWA
Installable, not offline-capable — see `docs/ROADMAP.md` "Future
possibilities" for exactly where that line is drawn and why. A web
manifest (`static/manifest.json`, served at `/manifest.json` — the site
root, not `/static/`, matters for the service worker's scope) declares
name/icons/`display: standalone`/theme color; a hand-drawn barbell icon
(`static/icons/icon.svg`, rasterized to the PNG sizes browsers actually
request) doubles as the app icon and favicon. The service worker
(`static/sw.js`) exists only to satisfy installability criteria and
cache-first genuinely static assets (CSS/JS/icons) — it explicitly never
intercepts a page, form submission, or HTMX response, so nothing here
can ever show stale workout data or silently swallow a logged set while
offline.
