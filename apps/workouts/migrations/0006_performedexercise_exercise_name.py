from django.db import migrations, models


def backfill_exercise_names(apps, schema_editor):
    """Rows logged before the snapshot existed get the exercise's current
    name — the best record left of what it was called at the time."""
    Exercise = apps.get_model("exercises", "Exercise")
    PerformedExercise = apps.get_model("workouts", "PerformedExercise")
    PerformedExercise.objects.filter(exercise_name="").update(
        exercise_name=models.Subquery(
            Exercise.objects.filter(pk=models.OuterRef("exercise_id")).values("name")[:1]
        )
    )


class Migration(migrations.Migration):

    dependencies = [
        ("exercises", "0001_initial"),
        ("workouts", "0005_exerciseset_unique_set_number"),
    ]

    operations = [
        migrations.AddField(
            model_name="performedexercise",
            name="exercise_name",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.RunPython(backfill_exercise_names, migrations.RunPython.noop),
    ]
