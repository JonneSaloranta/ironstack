from django.db import migrations, models


def renumber_duplicate_set_numbers(apps, schema_editor):
    """Give every set a unique number within its performed exercise.

    `log_set` used to number a new set `count() + 1`, so deleting a set
    and logging another could reuse a number a later set still held.
    Only performed exercises with such a collision are touched, and
    within them the existing order (set_number, then id) is kept - the
    numbers are just made consecutive again.
    """
    ExerciseSet = apps.get_model("workouts", "ExerciseSet")
    duplicated = (
        ExerciseSet.objects.values("performed_exercise_id", "set_number")
        .annotate(n=models.Count("id"))
        .filter(n__gt=1)
        .values_list("performed_exercise_id", flat=True)
        .distinct()
    )
    for performed_exercise_id in set(duplicated):
        sets = list(
            ExerciseSet.objects.filter(performed_exercise_id=performed_exercise_id).order_by(
                "set_number", "id"
            )
        )
        for number, exercise_set in enumerate(sets, start=1):
            if exercise_set.set_number != number:
                exercise_set.set_number = number
                exercise_set.save(update_fields=["set_number"])


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0003_resttimernotification"),
    ]

    operations = [
        migrations.RunPython(renumber_duplicate_set_numbers, migrations.RunPython.noop),
    ]
