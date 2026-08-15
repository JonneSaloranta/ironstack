from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Shared by every admin-only page under apps.core (Backups,
    Feedback, ...) — the same is_staff check that gates the admin-only
    cards on the profile page and Django's own /admin/ login."""

    def test_func(self):
        return self.request.user.is_staff
