import time

from django.core.management.base import BaseCommand

from apps.core.push import send_push_notification
from apps.workouts import services

# Rest periods are commonly adjusted in 15-second steps (the rest
# timer's own -15s/+15s buttons) and can run as short as a minute, so
# this needs to check far more often than
# apps.core.management.commands.backup_scheduler's own once-a-day
# loop does — a few seconds of worst-case lateness is an acceptable
# trade for not hammering the database, unlike that command's
# sleep-until-the-target-hour approach, which would be pointless here.
POLL_SECONDS = 3


class Command(BaseCommand):
    """Runs forever, sending a real Web Push for every RestTimerNotification
    whose `fire_at` has passed — docker-compose.yml's `notification-
    scheduler` service's only job. See RestTimerNotification's own
    docstring for why this exists at all: the rest timer's client-side
    "time's up" alert (static/js/rest-timer.js) can only fire while the
    page's JS is actually still running, which iOS Safari doesn't
    guarantee once the tab is backgrounded, let alone once the screen
    locks — a real push, delivered by the OS's own push service,
    doesn't have that problem.

    Deliberately not a real task queue (no Celery/Redis added just for
    this): the same reasoning as backup_scheduler's own docstring — a
    plain sleep-and-poll loop is enough for a self-contained Docker
    Compose stack, and this table is never more than one row per
    active user at a time.

    A single failed send (e.g. the push service briefly unreachable —
    already handled inside send_push_notification itself, which never
    raises) or a single bad row must never take the whole loop down,
    same reasoning as backup_scheduler's own try/except.
    """

    help = "Send a Web Push for every due rest-timer notification, forever."

    def handle(self, *args, **options):
        self.stdout.write("Rest timer dispatcher started.")
        while True:
            time.sleep(POLL_SECONDS)
            for notification in services.due_rest_timer_notifications():
                try:
                    send_push_notification(
                        notification.user,
                        notification.title,
                        notification.body,
                        url=notification.url or None,
                    )
                except Exception as exc:
                    # send_push_notification itself never raises (its
                    # own docstring) — this is belt-and-suspenders for
                    # anything unexpected in the query/delete around
                    # it, so one bad row can't stop every other user's
                    # notification from going out this tick.
                    self.stderr.write(
                        self.style.ERROR(
                            f"Rest timer notification failed for {notification.user}: {exc}"
                        )
                    )
                finally:
                    # Removed whether the send succeeded or not — a
                    # push that failed for a *transient* reason (the
                    # one case send_push_notification's own try/except
                    # doesn't already resolve to "give up permanently",
                    # e.g. a momentary timeout) is better dropped than
                    # retried once its fire_at has already passed:
                    # resending it a full POLL_SECONDS-plus later would
                    # land well after the rest period the user actually
                    # cared about.
                    notification.delete()
