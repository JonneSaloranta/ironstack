from django.db import migrations, models


def mark_existing_users_chosen(apps, schema_editor):
    # Existing accounts already live in their stored language — keep it,
    # rather than switching them to whatever their browser says.
    apps.get_model("accounts", "User").objects.update(language_chosen=True)


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0019_user_stretching_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="language_chosen",
            field=models.BooleanField(default=False),
        ),
        migrations.RunPython(mark_existing_users_chosen, migrations.RunPython.noop),
    ]
