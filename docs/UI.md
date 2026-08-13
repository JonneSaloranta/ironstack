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
weight, and an in-progress-workout banner that doubles as "continue/last
workout". Two items from this doc's original wishlist were deliberately
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
(dashboard), Workout (session history/start), Programs, Progress (body
tracking), Profile (unit/timezone preferences, password change — added
Phase 11, previously a dead link since Phase 1).

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
