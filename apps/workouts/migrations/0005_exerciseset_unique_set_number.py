from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workouts", "0004_renumber_duplicate_set_numbers"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="exerciseset",
            constraint=models.UniqueConstraint(
                fields=("performed_exercise", "set_number"),
                name="unique_set_number_per_performed_exercise",
            ),
        ),
    ]
