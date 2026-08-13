"""No models here on purpose — every analytics view is computed live from
apps.workouts/apps.records/apps.measurements/apps.activities history
(see services.py), the same "derive, don't cache" approach used
throughout this project's other computed-analysis apps
(apps.records, apps.progression). docs/ANALYTICS.md: "only add
denormalized/cached aggregates when profiling demonstrates a need."
"""
