"""Translation catalog for seeded *content*, not UI chrome — built-in
program template names, descriptions, and workout names
(apps.programs.migrations' seed data) are stored in the database in
canonical English, the value `get_or_create(name=...)` matches against
elsewhere, so the stored value itself must never be translated. This
module exists solely so `makemessages` extracts these exact strings
into the `.po` catalog; nothing here is ever imported or executed for
its return value — see apps.exercises.i18n_content and
docs/ARCHITECTURE.md "Internationalization" for the full pattern
(`{% trans program.name %}` etc. at render time).
"""

from django.utils.translation import gettext_lazy as _

# Program template names (apps.programs.migrations 0002, 0004, 0006).
PROGRAM_NAMES = [
    _("Full Body A/B/C"),
    _("Arnold Split (6-Day)"),
    _("Push/Pull/Legs"),
    _("5x5 Strength (A/B)"),
    _("Upper/Lower Split (4-Day)"),
    _("German Volume Training"),
]

# Program template descriptions (same migrations as above).
PROGRAM_DESCRIPTIONS = [
    _(
        "A classic three-day full-body template: one squat/press/row day, "
        "one deadlift/press/pull day, and one accessory-focused day. Copy "
        "it and adjust weights/increments to your own."
    ),
    _(
        "The classic 6-day bodybuilding split popularized by Arnold "
        "Schwarzenegger in the 1970s: chest/back, shoulders/arms, and "
        "legs, each trained twice a week. Rotate through all three "
        "workouts, then repeat — not tied to fixed calendar days."
    ),
    _(
        "A widely used 3-day split organized by movement pattern "
        "rather than body part: pushing muscles, pulling muscles, "
        "then legs. Popular for both strength and bodybuilding goals."
    ),
    _(
        "A classic beginner barbell strength program: two alternating "
        "full-body workouts built around low-rep, high-set compound "
        "lifts with linear weight progression each session."
    ),
    _(
        "A 4-day split alternating upper-body and lower-body sessions "
        "twice each per week — a common middle ground between full-body "
        "and single-muscle-group routines, popular for balancing "
        "strength and volume without training every day."
    ),
    _(
        "The classic 10x10 high-volume method: one main lift per "
        "workout done for ten sets of ten reps at a fixed, moderate "
        "weight, paired with a lighter accessory movement. Brutal on "
        "paper, simple in practice — built for muscle growth, not "
        "1-rep-max testing."
    ),
]

# Workout names within the templates above (deliberately deduplicated —
# "Workout A"/"Workout B"/"Workout C" are reused across several
# templates, so they only need one catalog entry each).
WORKOUT_NAMES = [
    _("Workout A"),
    _("Workout B"),
    _("Workout C"),
    _("Chest & Back"),
    _("Shoulders & Arms"),
    _("Legs"),
    _("Push"),
    _("Pull"),
    _("Upper A"),
    _("Lower A"),
    _("Upper B"),
    _("Lower B"),
]
