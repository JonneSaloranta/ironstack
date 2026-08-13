"""No models here on purpose.

A progression decision is recomputed live from ExerciseSet/PersonalRecord
history each time (see engine.py), the same "derive, don't cache" approach
apps.records uses for PRs — there is no new state for this app to persist.
"""
