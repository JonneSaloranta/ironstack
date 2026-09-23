# Stretching

`apps.stretching` is a stretching and mobility tracker that sits
alongside training (`apps.workouts`), activities and nutrition. It
covers a stretch library, reusable routines, a guided session player
with a timer, quick after-the-fact logging, and history and stats.
Like the rest of IronStack, it is a log first and an assistant second:
the timer and the post-workout suggestion only propose, and the user
always decides.

## Domain model

| Model | Purpose |
|---|---|
| `Stretch` | One stretch in the library. The built-in stretches are seeded with `owner=None`, and users can add their own (`owner=user`). The ownership, uniqueness and soft-delete (`active`) pattern is the same as `ActivityType`. Fields: `kind` (static/dynamic), `per_side`, `default_hold_seconds`, `instructions`, and `muscle_groups`, which is an M2M to `exercises.MuscleGroup`. |
| `StretchRoutine` | An editable, ordered list of stretches, owned the same system-or-own way as `Stretch`. |
| `RoutineItem` | One stretch in a routine, with `hold_seconds`, `sets`, `rest_seconds` and `order`. `stretch` is `PROTECT`. |
| `StretchSession` | One session. It is either guided (started from a routine or a cool-down suggestion) or quick-logged with just a duration. `status` is in_progress/completed/abandoned, and a partial unique constraint allows only one in-progress session per user. `after_workout` links a cool-down to the `WorkoutSession` it followed. |
| `PerformedStretch` | The snapshot of each planned stretch, taken when the session starts: name, per-side, hold, sets and rest. It also records `status` (pending/done/skipped) and `actual_seconds`. |

### Historical integrity

Starting a session copies everything the session needs from its
routine into its own rows (`services.start_session`). This is the same
snapshot-on-start rule `apps.workouts` follows. Editing, retiring or
deleting a routine later never changes what a past session says
happened. `PerformedStretch.stretch` is only a backlink, used for
instructions while the session plays. `StretchSession.duration` is the
wall-clock time from start to finish for a guided session, or the
user's own figure for a quick log.

### Seeded content

The seed migration `0002` loads 32 stretches and 4 routines from
`seed_data/stretches.json`: two cool-downs (lower and upper body), a
full-body stretch, and a morning mobility routine. The instructions
are original text written for this project. The stored names and text
stay in canonical English and are translated at render time with
`|translate_content`. `i18n_content.py` lists every seeded string for
`makemessages`, and `StretchingSeedTests` fails if one is missing (see
`ARCHITECTURE.md` → "Internationalization").

Muscle groups reuse the existing `MuscleGroup` rows, so there's no
"hip flexor" or "neck" group. Those stretches are tagged with the
nearest existing group (Quads for the hip flexors, Traps for the neck).

## Services

`apps/stretching/services.py` holds all the logic:

- **Visibility**: `visible_stretches` and `visible_routines` return
  system rows plus the user's own rows.
- **Routines**: `add_routine_item`, `move_routine_item`, and
  `copy_routine`. A built-in routine is read-only, so "Copy to edit"
  gives the user their own copy, with the text translated into their
  language.
- **Sessions**: `start_session`, `mark_performed`, `complete_session`,
  `abandon_session` and `quick_log`. `start_session` raises
  `SessionAlreadyInProgress` if the user already has a session running.
- **`timer_steps(session)`**: the guided player's playlist. For each
  pending stretch it produces a 5-second "prepare" step, one "hold"
  step per set × side, and "rest" steps between holds. The step list
  is built in Python so it can be tested there; the JS only plays it.
- **`suggest_cooldown(workout_session)`**: see below.
- **Stats**: `summarize` (count, total time, last 7 days, and a streak
  of consecutive days ending today or yesterday), `weekly_minutes` /
  `weekly_chart` (the last 8 ISO weeks, empty weeks included), and
  `calendar_minutes` (at least 1 minute for any day with a session).

### Cool-down suggestion

The suggestion starts from the primary muscle groups of the exercises
in a finished workout. The routine whose **static** stretches cover
the largest share of those groups wins, and ties go to the shorter
routine. Dynamic stretches are left out so the warm-up routine is
never suggested as a cool-down. The winning routine is used if it
covers at least half of the trained groups. Otherwise the suggestion
is a list of static stretches from the library, one per trained group
and at most six. Nothing is ever started automatically: the card on
the workout page only offers a button.

## UI

- The section has its own bottom-nav tab (icon only on mobile). Inside
  the section, a four-tab sub-nav links to Overview, Routines,
  Stretches and History.
- **Guided player** (`session_play.html` + `static/js/stretch-timer.js`):
  - Shows a large clock, the phase (get ready / hold / rest), the
    side, and the set.
  - Chimes and vibrates when the phase changes. A hold starting gets a
    distinct double chime.
  - Takes a screen wake lock so the phone doesn't lock mid-hold.
  - Has ±10 s, pause/resume, next step and skip stretch controls.
  - Uses the same wall-clock deadline as the rest timer, so a locked
    screen doesn't make it drift.
  - When a stretch's last hold finishes, it POSTs that stretch (JSON)
    as done with the seconds actually held. A page reload then resumes
    at the next pending stretch.
  - Every stretch also has plain Done/Skip forms that work without JS.
- The shared chime, iOS audio unlock and vibration live in
  `static/js/timer-audio.js`, which the workout rest timer also uses.
  Each timer keeps its own mute setting in localStorage.

## Integration with other apps

- **`User.stretching_enabled`** (default on): the same "only hides the
  door" toggle as `nutrition_enabled`. It hides the nav tab, the
  dashboard link card, the calendar dot and the cool-down card. The
  stretching pages themselves stay reachable, and no data is touched.
  It can be changed in onboarding and in Profile → Preferences →
  Features.
- **Workout page**: `{% cooldown_card session %}`
  (`stretching_extras`) is an inclusion tag, so `apps.workouts` never
  imports `apps.stretching`. The dependency only points
  stretching → workouts.
- **Dashboard**: `apps.core.views._month_calendar_context` adds a
  corner dot and a "Stretched N min." popover line to each day with a
  finished session.
- **API**: `stretches/`, `stretch-routines/`, `stretch-routine-items/`
  and `stretch-sessions/`, all under the `stretching` key context (see
  `API.md`). Creating a session through the API is a quick log.
- **Account data**: the GDPR export includes sessions, performed
  stretches, and the user's own stretches, routines and routine items.
  Account deletion *deletes* the user's own stretches and routines
  instead of reassigning them to `owner=None` like shared content,
  because nobody else can see them. Routines are deleted first because
  `RoutineItem.stretch` is `PROTECT`.

## Not built yet

- No weekly stretching schedule or plan (unlike programs' weekdays).
- No server-side push when a hold ends. Holds are short and the page
  is normally on screen (with a wake lock), so the rest timer's push
  backstop wasn't worth it here.
- No coach sharing or export/import of routines.
