# Analytics

Analytics should provide useful statistical information without becoming a collection of decorative charts.

## Strength analytics

Track:
- estimated 1RM over time
- max weight over time
- reps
- sets
- volume
- tonnage
- exercise frequency
- PR history
- intensity where meaningful

## Training analytics

Track:
- workouts per week
- workouts per month
- training frequency
- session duration
- total training time
- muscle-group volume
- exercise frequency
- consistency

## Set cycle time

How long a set takes, for estimating a workout's length
(`apps/workouts/set_timing.py`, tests in `apps/workouts/test_set_timing.py`).

- **What is measured.** Only a set's completion is recorded
  (`ExerciseSet.performed_at`), so the measurable unit is the *set cycle*:
  the gap between two consecutive logged sets of one exercise - the set,
  the rest and changing the weights. The gap into an exercise's first set
  (finishing the previous one, setting up) is a *transition*.
- **Whose data.** Every user's completed workouts on the instance, last two
  years. A group is used once it has 20 gaps, from however many users (an
  instance often has one). Each user contributes at most their latest 50
  gaps per group, so one very active user doesn't decide the median.
- **Dropped before anything is computed** (hard limits): gaps under 10 s
  (double tap, sets typed in afterwards) or over 20 min (forgot to log,
  workout left open); sets logged under 5 s apart; sets whose
  `performed_at` is more than 60 s from `created_at` (edited or supplied
  later, e.g. by the API); a set of another exercise in between (supersets
  and circuits); warm-up sets; unfinished workouts.
- **Off the curve** (statistical): Tukey's fences, 1.5 x IQR, on the log of
  the gaps in each group. Gaps are right-skewed, so on a log scale the
  fences remove misfires and forgotten timers without trimming normal long
  rests. The fences are never narrower than 0.2 on the log scale, or a group
  of identical gaps would drop everything that differs.
- **Groups**, most to least specific: exercise x rep range (1-3, 4-6, 7-10,
  11-15, 16+), exercise, movement type x rep range, every exercise x rep
  range, then a default (120 s + 3 s per rep, quartiles -25 % / +30 %;
  transition 180 s), which the UI labels "rough - little data yet".
- **Shown as "x ± y".** One set: the median ± half the interquartile range
  (the middle half of the sets). A workout: one transition per exercise and
  a cycle for each further set; the low and high ends are the sums of the
  quartiles, shown as their midpoint ± half the range. Summed directly
  rather than as variances: one person tends to be slow or fast on every
  set, so the uncertainty doesn't cancel out.
- **Where.** A program's workout card ("Estimated duration"), training mode
  ("Time left", remaining sets only), and the exercise page's "Set cycle"
  card: the user's own figure (from 5 gaps) and everyone's, which is shown
  only when the data comes from more than one user.
- **Cost.** The instance-wide groups are computed in one windowed query
  (`LAG` over each exercise's and each session's sets) and cached for 6 h
  (`set_timing.clear_cache()` resets it). Warm-up time isn't modelled.

## Body analytics

Track:
- body weight trend
- body fat trend
- circumference trends
- custom measurement trends

## Activity analytics

Track:
- activity minutes
- distance
- frequency
- activity type distribution

## Date ranges

Support:
- 7 days
- 30 days
- 3 months
- 6 months
- 1 year
- all time
- custom range

## Charts

Use charts when they communicate trends or comparisons clearly.

Examples:
- line chart: estimated 1RM over time
- line chart: body weight over time
- bar chart: weekly training volume
- line chart: exercise strength trend
- bar chart: muscle-group volume
- line chart: activity duration

Always provide important values in text as well.

## Performance

Analytics queries can become expensive.

Start with straightforward ORM queries and indexes.

Only add denormalized/cached aggregates when profiling demonstrates a need.

Analytics must always respect user ownership.

## Implementation

Body/circumference/custom-measurement trends and activity minutes/
distance/frequency already got dedicated, working pages in Phases 8-9
(`apps.measurements`, `apps.activities`) - `apps.analytics` doesn't
duplicate those. What it adds:

- `/analytics/` - a dashboard: training summary (workouts, training
  time, total volume), a weekly training volume bar chart, a
  muscle-group volume bar chart (a set's full volume counts toward every
  primary muscle group its exercise targets - the simplest defensible
  split, rather than dividing fractionally with no principled basis),
  and PR history (reuses `apps.records`' immutable achievement log,
  grouped by exercise - `apps.analytics.services.
  pr_history_grouped_by_exercise` - rather than one card per PR row,
  since a single session can produce several `record_type` rows for
  the same exercise).
- `/analytics/exercises/<pk>/` - per-exercise strength trend: estimated
  1RM over time (one point per session - that session's best estimate,
  not one point per set, so the trend stays readable) plus session
  count/volume in range.
- Date-range filtering (`apps.analytics.dateranges`) is shared by both:
  presets resolve relative to today; an explicit `start`/`end` pair (the
  "custom range" requirement) overrides any preset.
- Training-load volume (here) intentionally counts failed sets - the
  work still happened - unlike `apps.records`' PR eligibility, which
  requires a clean, successful set to count as a record.
- `apps.core.charts` gained `build_bar_series` alongside the existing
  `build_chart_series` (Phase 8/9), so bar and line charts share the
  same model-agnostic, tested foundation.

Also feeds `docs/UI.md`'s dashboard content list directly:
`apps.core.views.DashboardView` now shows this week's volume, the 3 most
recent PRs, and the latest body-weight reading, alongside the existing
in-progress-workout banner.

### Chart titles/legends audit (post-Phase 11)

Every chart in the app is single-series (one line, or one set of
same-colored bars), which per the dataviz skill's own rule never needs a
legend box - but only if the chart's title is actually visible to a
sighted user, not just present for screen readers. An audit of every
`core/_chart.html`/`core/_bar_chart.html` usage found two real gaps and
fixed both:

- Three line-chart pages (measurements, activities, per-exercise
  strength trend) carried their title only in the SVG `aria-label`,
  invisible on the page itself. Each now has a visible `<h2>` heading
  directly above the chart, matching the pattern the analytics dashboard's
  bar charts already used correctly. The activities page's heading was
  also wrong in substance, not just missing: it showed the activity type
  name (e.g. "Running") where "Duration trend" is what's actually
  plotted.
- The bar charts (weekly volume, muscle-group volume) had **no visible
  category labels at all** - no x-axis text, no legend, and (despite a
  code comment claiming otherwise) no table - only a per-bar hover
  tooltip, which isn't discoverable on touch devices. `core/_bar_chart.html`
  now renders a small table (category, value) directly below every bar
  chart - not a legend, since a categorical color key wouldn't have
  helped when every bar is deliberately the same color (see the file's
  own reasoning for why).
- Follow-up: even with the table, an unlabeled row of same-colored bars
  read as broken rather than minimal, so each bar now also carries its
  own rotated `<text>` label directly in the SVG
  (`apps.core.charts.build_bar_series` reserves a label band below the
  plot area and computes each bar's label position) - the table stays
  as the precise, always-accessible source of the exact figures, but
  the chart itself is readable at a glance now too.
