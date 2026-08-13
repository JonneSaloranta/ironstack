# IronStack

Self-hosted, mobile-first fitness and activity tracker. See `docs/` for the
full product/architecture/domain specification and `CLAUDE.md` for the
project's development guidelines.

## Status

Phase 1 (Foundation) — Django project scaffold, custom user model,
authentication, mobile-first base layout, Docker Compose stack.

Phase 2 (Exercises) — exercise library (`apps/exercises`): system-seeded
muscle groups, equipment, and a starter exercise set; user-created custom
exercises (private to their owner); browse/search/filter UI with HTMX-backed
live filtering; soft-delete via an `active` flag so history stays intact
after an exercise is retired.

## Local development

```bash
cp .env.example .env   # then edit secrets
docker compose up --build
```

This starts PostgreSQL, the Django dev server (`runserver`, auto-reload) at
http://localhost:8000, and nginx in front of it on http://localhost.

Run tests:

```bash
docker compose exec web pytest
```

Create an admin user:

```bash
docker compose exec web python manage.py createsuperuser
```

### Without Docker

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env  # set POSTGRES_HOST=localhost
python manage.py migrate
python manage.py runserver
```

## Production

`docker-compose.override.yml` is a **local development** file that Docker
Compose merges in automatically. Do not ship it to a server. On the
production host, only `docker-compose.yml` should be present, and `.env`
must set real secrets, `DJANGO_ALLOWED_HOSTS`, etc. — see
`docs/SECURITY.md`.

```bash
docker compose -f docker-compose.yml up -d --build
```

## Tests & linting

```bash
ruff check .
pytest
```
