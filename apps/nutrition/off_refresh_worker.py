"""Works through the Open Food Facts refresh queue in the background.

FoodAdmin's "Refresh selected foods" actions used to refresh inside the
admin request, which could only spend one minute's read budget (12
foods) and left the admin to find and re-select the rest. Now they just
queue the foods (`Food.off_refresh_requested_at` /
`price_refresh_requested_at`, see services.queue_off_refresh) and start
this worker as a detached `manage.py refresh_off_queue` process - the
same shape as apps.core.backup_tasks - which drains the queue at
WORKER_READS_PER_MINUTE and exits once it's empty.

The worker spends only half of off_http's read budget, so users' own
live lookups keep working while a large refresh runs. When the budget
is spent anyway (or OFF failed recently) it waits a minute and retries
the same food - nothing is skipped. The queue lives in the database, so
a container restart loses nothing; the next admin action, or running
the command by hand, carries on where it stopped.
"""

import subprocess
import sys
import threading
import time

from django.conf import settings
from django.core.cache import cache

from . import off_http

LOCK_KEY = "nutrition:off_refresh_worker"
# Re-set on every loop; a worker that died lets it lapse this quickly.
LOCK_SECONDS = 120

WORKER_READS_PER_MINUTE = off_http.READ_LIMIT_PER_MINUTE // 2
SECONDS_BETWEEN_READS = 60 / WORKER_READS_PER_MINUTE
RATE_LIMITED_WAIT_SECONDS = 60


def is_running():
    return cache.get(LOCK_KEY) is not None


def ensure_running():
    """Start the worker unless one already is."""
    if is_running():
        return
    if settings.OFF_REFRESH_RUN_INLINE:
        # Tests only: no pauses, and stop at the first rate limit
        # instead of waiting for the next minute.
        run(sleep=lambda seconds: None, stop_when_rate_limited=True)
        return
    process = subprocess.Popen(
        [sys.executable, "manage.py", "refresh_off_queue"],
        cwd=settings.BASE_DIR,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    # Reaps the child once it exits so it doesn't linger as a zombie.
    threading.Thread(target=process.wait, daemon=True).start()


def run(*, sleep=time.sleep, stop_when_rate_limited=False):
    """Drain the queue; returns how many refreshes were attempted.
    Returns 0 straight away if another worker holds the lock."""
    from . import services

    if not cache.add(LOCK_KEY, True, LOCK_SECONDS):
        return 0
    attempted = 0
    try:
        while True:
            cache.set(LOCK_KEY, True, LOCK_SECONDS)
            outcome = services.process_next_queued_refresh()
            if outcome == services.REFRESH_QUEUE_EMPTY:
                break
            if outcome == services.REFRESH_RATE_LIMITED:
                if stop_when_rate_limited:
                    break
                sleep(RATE_LIMITED_WAIT_SECONDS)
                continue
            attempted += 1
            sleep(SECONDS_BETWEEN_READS)
    finally:
        cache.delete(LOCK_KEY)
    return attempted
