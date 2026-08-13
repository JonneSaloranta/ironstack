"""Translation catalog for seeded *content*, not UI chrome — exercise
names, muscle group names, and equipment names (apps.exercises.migrations'
seed data) are stored in the database in canonical English, the same
value used for `get_or_create(name=...)` lookups and uniqueness
constraints elsewhere, so the stored value itself must never be
translated. This module exists solely so `makemessages` extracts these
exact strings into the `.po` catalog; nothing here is ever imported or
executed for its return value. Templates then look the *display* value
up in that catalog at render time via `{% trans exercise.name %}`
(Django's `trans` tag accepts a variable, not just a literal — it runs
the variable's resolved value through `gettext()`), which is a no-op
for a string with no catalog entry (a user's own custom exercise, never
in this list, just renders as typed) and a real translation for any of
these seeded names — see docs/ARCHITECTURE.md "Internationalization".
"""

from django.utils.translation import gettext_lazy as _

# Exercise names (apps.exercises.migrations 0002, 0004).
EXERCISE_NAMES = [
    _("Ab Wheel Rollout"),
    _("Barbell Back Squat"),
    _("Barbell Bench Press"),
    _("Barbell Row"),
    _("Calf Raise"),
    _("Conventional Deadlift"),
    _("Dumbbell Bench Press"),
    _("Dumbbell Bicep Curl"),
    _("Dumbbell Shoulder Press"),
    _("Face Pull"),
    _("Front Squat"),
    _("Hammer Curl"),
    _("Hip Thrust"),
    _("Incline Barbell Bench Press"),
    _("Lat Pulldown"),
    _("Lateral Raise"),
    _("Leg Curl"),
    _("Leg Press"),
    _("Overhead Press"),
    _("Plank"),
    _("Pull-Up"),
    _("Romanian Deadlift"),
    _("Seated Cable Row"),
    _("Skull Crusher"),
    _("Triceps Pushdown"),
]

# Muscle group names (apps.exercises.migrations 0002).
MUSCLE_GROUP_NAMES = [
    _("Abs"),
    _("Back"),
    _("Biceps"),
    _("Calves"),
    _("Chest"),
    _("Forearms"),
    _("Glutes"),
    _("Hamstrings"),
    _("Quads"),
    _("Shoulders"),
    _("Triceps"),
]

# Equipment names (apps.exercises.migrations 0002).
EQUIPMENT_NAMES = [
    _("Barbell"),
    _("Bodyweight"),
    _("Cable"),
    _("Dumbbell"),
    _("Kettlebell"),
    _("Machine"),
    _("Resistance Band"),
]
