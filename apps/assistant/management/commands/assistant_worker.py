import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

from apps.assistant import services

# A user is watching the reply appear, so pick new messages up quickly;
# an idle poll is one indexed query.
POLL_SECONDS = 1


class Command(BaseCommand):
    """Runs forever, writing assistant replies — docker-compose.yml's
    `assistant-worker` service. Replies run here rather than in a web
    request because a reply with a few tool calls easily takes longer
    than gunicorn's 30-second worker timeout; the page polls the message
    while this streams text into it (docs/ASSISTANT.md "How a reply is
    written").

    Not a real task queue, for the same reasons as rest_timer_dispatcher
    and backup_scheduler: a sleep-and-poll loop over one table is enough
    for a self-contained Compose stack. Several copies can run at once —
    services.process_message claims each message with SELECT ... FOR
    UPDATE SKIP LOCKED — so `docker compose up --scale assistant-worker=2`
    handles more concurrent users.
    """

    help = "Write pending AI assistant replies, forever."

    def handle(self, *args, **options):
        self.stdout.write("Assistant worker started.")
        while True:
            close_old_connections()
            try:
                services.fail_stale_messages()
                message_ids = services.pending_message_ids()
                for message_id in message_ids:
                    # process_message handles every error a reply can hit
                    # itself; this only guards the loop against the
                    # unexpected (a lost database connection).
                    services.process_message(message_id)
            except Exception as exc:
                self.stderr.write(self.style.ERROR(f"Assistant worker error: {exc}"))
                message_ids = []
            if not message_ids:
                time.sleep(POLL_SECONDS)
