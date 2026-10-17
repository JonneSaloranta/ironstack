from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin


class StaffRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """Shared by every admin-only page under apps.core (Backups,
    Feedback, ...) - the same is_staff check that gates the admin-only
    cards on the profile page and Django's own /admin/ login."""

    def test_func(self):
        return self.request.user.is_staff


class SuperuserRequiredMixin(LoginRequiredMixin, UserPassesTestMixin):
    """For pages that hand out or replace the whole instance - Backups:
    a backup holds every user's data and password hashes, and a restore
    replaces all of it, so `is_staff` (which can be granted for
    moderating feedback or reference data) isn't enough."""

    def test_func(self):
        return self.request.user.is_superuser
