from django.conf import settings
from django.db import models


class TutorialCompletion(models.Model):
    """A user has finished or skipped one tour (`apps.tutorials.tours`),
    so it isn't started automatically for them again. `tour` is the tour's
    registry key, not a foreign key: tours live in code, not the database.
    Deleting the row (profile → Tutorials → "Show again") re-arms it."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, related_name="tutorial_completions", on_delete=models.CASCADE
    )
    tour = models.CharField(max_length=64)
    completed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["user", "tour"], name="unique_tutorial_completion"),
        ]

    def __str__(self):
        return f"{self.user.username}: {self.tour}"
