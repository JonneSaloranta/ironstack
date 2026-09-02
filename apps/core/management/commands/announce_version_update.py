from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.urls import reverse
from django.utils.translation import gettext as _

from apps.core.models import AnnouncedVersion, PushSubscription
from apps.core.push import send_push_notification
from apps.core.version import get_version


class Command(BaseCommand):
    """Sends a "new version is available" push notification to every
    user with at least one push subscription, but only once per real
    version bump — run unconditionally on every `web` container start
    (`docker-compose.yml`), it compares the running `VERSION`
    (`apps.core.version.get_version()`) against `AnnouncedVersion`'s
    own stored value and is a silent no-op whenever they already
    match, which is true on every restart except the one right after a
    genuine deploy. See `AnnouncedVersion`'s own docstring for why
    comparing against a stored version — rather than e.g. "did the
    container just start" — is what makes this safe to run
    unconditionally instead of needing its own separate trigger.

    Notifies every user with a subscription, not just users who were
    active recently or opted into some separate "release notes"
    preference: subscribing to push at all (Profile → Notifications)
    already is that opt-in, the same bar `send_direct_message`/
    `send_group_message` use for message notifications, and there's no
    separate mute for this one kind of push the way a friend or group
    can be muted — a new version shipping isn't tied to any single
    friend or group to mute in the first place.

    Runs synchronously and before gunicorn starts (same startup step
    as `migrate`/`collectstatic`), not from a request — so unlike
    `send_push_notification`'s other callers, there's no request this
    could ever delay. Still bounded the same way (5s timeout per
    device, inside `send_push_notification` itself): worst case is a
    few extra seconds of container startup with many subscribed users,
    not a hung deploy, acceptable at this app's expected personal/
    small-group scale (see docs/SECURITY.md "Web Push notifications").
    """

    help = 'Sends a "new version available" push notification once per real VERSION bump.'

    def handle(self, *args, **options):
        current_version = get_version()
        announced = AnnouncedVersion.load()
        if announced.version == current_version:
            self.stdout.write(f"Version {current_version} already announced — nothing to do.")
            return

        title = _("New IronStack version")
        body = _("IronStack %(version)s is now running — tap to see what's new.") % {
            "version": current_version
        }
        url = reverse("profile") + "?changelog=1"
        user_ids = PushSubscription.objects.values_list("user_id", flat=True).distinct()
        notified = 0
        for user in get_user_model().objects.filter(id__in=user_ids):
            send_push_notification(user, title, body, url=url)
            notified += 1

        announced.version = current_version
        announced.save()
        self.stdout.write(f"Announced version {current_version} to {notified} user(s).")
