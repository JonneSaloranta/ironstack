#!/bin/sh
# Every one-time-per-deploy setup step (migrations, cache table,
# static files, translations, the "new version" push notification —
# apps.core.management.commands.announce_version_update) lives here,
# baked into the image, instead of docker-compose.yml's own `command:`
# the way it used to be. See docs/ARCHITECTURE.md "Versioning" for
# why: a step added here ships with every `docker compose pull` of a
# new image, with no separate need to also update whichever compose
# file happens to be running it. The previous `sh -c "migrate && ...
# && gunicorn"` chain lived entirely in docker-compose.yml — a real
# problem the first time this project ever added a step to that chain
# (announce_version_update, 1.9.0): a hand-maintained production copy
# of that file silently fell out of sync, and the new step never ran
# there until noticed and fixed by hand.
#
# Only for gunicorn ($1 = "gunicorn", the `web` service's own
# command) — skipped for every other command sharing this same image
# and ENTRYPOINT: `backup-scheduler`'s `python manage.py
# backup_scheduler`, docker-compose.override.yml's own dev `sh -c
# "migrate && ... && runserver"` chain (deliberately its own separate,
# shorter chain — no collectstatic/announce_version_update in dev),
# or an operator's own one-off `docker compose run web python
# manage.py shell`. None of those need a fresh migration/static/
# translation pass — or a real push notification to every subscribed
# user — just to start.
set -e

if [ "$1" = "gunicorn" ]; then
    python manage.py migrate --noinput
    python manage.py createcachetable
    python manage.py collectstatic --noinput
    python manage.py compilemessages
    python manage.py announce_version_update
fi

exec "$@"
