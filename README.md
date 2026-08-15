# IronStack

Self-hosted, mobile-first fitness and activity tracker — a training log
first, an intelligent assistant second. Log workouts, follow a program,
get explainable weight suggestions, track PRs automatically, and see
your progress in real charts — all on your own infrastructure.

[![CI](https://github.com/JonneSaloranta/ironstack/actions/workflows/ci.yml/badge.svg)](https://github.com/JonneSaloranta/ironstack/actions/workflows/ci.yml)
[![License: AGPL v3](https://img.shields.io/badge/License-AGPLv3-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Django 5](https://img.shields.io/badge/django-5.x-092E20.svg)](https://www.djangoproject.com/)
[![i18n](https://img.shields.io/badge/i18n-6%20languages-informational.svg)](docs/ARCHITECTURE.md#internationalization)

## Features

- **Workout logging** — fast, HTMX-driven set entry that pre-fills from
  your last set; works fully as plain forms with JavaScript off, too.
- **Programs & templates** — build your own program or start from a
  built-in template (5×5, Push/Pull/Legs, Arnold Split, ...); copying a
  template never touches the original, and editing a program never
  rewrites history already logged against it.
- **Automatic PR detection** — max weight, rep PRs, estimated 1RM, set
  and session volume, detected live off your real history, no caching.
- **Explainable smart weight suggestions** — seven progression methods
  (linear, double progression, RPE/RIR, percentage-based, ...), always
  shown with a plain-language reason and confidence — always just a
  form default you can override, never a decision made for you.
- **Body & activity tracking** — measurements (weight, body fat %,
  circumferences, ...) and manually logged activities (runs, rides,
  anything), each with its own trend chart.
- **Analytics dashboard** — weekly training volume, muscle-group
  volume, PR history, per-exercise strength trends, custom date ranges.
- **A real API** — per-user API keys with per-resource CRUD
  permissions and admin-tunable rate limits, for anything you want to
  build against your own data. See [`docs/API.md`](docs/API.md).
- **Installable PWA** — add it to your home screen; static assets are
  cached for speed, but nothing about your actual training data ever
  is, so you never see stale history.
- **Automatic backups** — a daily scheduled backup plus an admin-only
  web UI and host-side scripts to create, download, and restore full
  backups on demand. See [`docs/BACKUP.md`](docs/BACKUP.md).
- **Translated** — English, Finnish, Swedish, Russian, Italian, and
  Estonian, UI chrome and seeded content alike.

## Quick start

```bash
git clone https://github.com/JonneSaloranta/ironstack.git
cd ironstack
cp .env.example .env
docker compose up --build
```

Open **http://localhost:8000** (nginx also fronts it on **http://localhost**).
Then create an admin account:

```bash
docker compose exec web python manage.py createsuperuser
```

That's it — PostgreSQL, migrations, and the Django dev server (with
auto-reload) are all running. The default `.env` works out of the box
for local development; nothing needs editing to get started.

### Without Docker

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements/dev.txt
cp .env.example .env   # set POSTGRES_HOST=localhost
python manage.py migrate
python manage.py runserver
```

## Tech stack

| Layer | Choice |
|---|---|
| Backend | Python, Django, PostgreSQL |
| Frontend | Django Templates, HTMX, Alpine.js — server-rendered first, no SPA framework |
| API | Django REST Framework, API-key auth, per-key rate limiting |
| Deployment | Docker Compose — app, PostgreSQL, reverse proxy (nginx, or Caddy for automatic TLS) |
| i18n | Django's own gettext catalogs — no third-party translation library |

No JavaScript build step, no Node dependency, no SPA framework — pages
render on the server; HTMX and Alpine.js add just the interactivity a
given page actually needs.

## Documentation

| Doc | Covers |
|---|---|
| [`docs/PRODUCT_REQUIREMENTS.md`](docs/PRODUCT_REQUIREMENTS.md) | The original product spec |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | App structure, stack decisions, versioning |
| [`docs/DOMAIN_MODEL.md`](docs/DOMAIN_MODEL.md) | The core data model |
| [`docs/DEVELOPMENT.md`](docs/DEVELOPMENT.md) | Phase-by-phase implementation plan and acceptance criteria |
| [`docs/PROGRESSION.md`](docs/PROGRESSION.md) | The seven progression methods |
| [`docs/SMART_SUGGESTIONS.md`](docs/SMART_SUGGESTIONS.md) | How weight suggestions are composed and explained |
| [`docs/PR_SYSTEM.md`](docs/PR_SYSTEM.md) | The six PR types and how they're detected |
| [`docs/ANALYTICS.md`](docs/ANALYTICS.md) | Dashboard, charts, date-range filtering |
| [`docs/API.md`](docs/API.md) | REST API — auth, permissions, rate limits, endpoints |
| [`docs/UI.md`](docs/UI.md) | UI principles and per-feature implementation notes |
| [`docs/SECURITY.md`](docs/SECURITY.md) | TLS, email, rate limiting, CSP, and everything else before going to production |
| [`docs/BACKUP.md`](docs/BACKUP.md) | Backup/restore, both mechanisms, in full |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | What's deliberately not built yet, and why |
| [`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md) | The detailed, ongoing build history |
| [`CHANGELOG.md`](CHANGELOG.md) | What changed, by version |
| [`CLAUDE.md`](CLAUDE.md) | Project conventions and guidelines for AI-assisted development |

## Testing & linting

```bash
ruff check .
pytest
```

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs the same
two checks — plus a missing-migrations check and compiling the locale
catalogs first — on every push and pull request against `master` and
`dev`, against a real `postgres:16-alpine` service container.
[`.github/dependabot.yml`](.github/dependabot.yml) opens a weekly,
reviewed PR for outdated pip/Docker/GitHub Actions dependencies, still
gated by that same CI.

## Production deployment

`docker-compose.override.yml` is a **local development** file Docker
Compose merges in automatically — do not ship it to a server. On the
production host, only `docker-compose.yml` should be present, with
`.env` set to real secrets, `DJANGO_ALLOWED_HOSTS`, etc.

```bash
docker compose -f docker-compose.yml up -d --build
```

**Before your first deploy, read [`docs/SECURITY.md`](docs/SECURITY.md)'s
"TLS" section** — the bundled nginx config is HTTP-only, and the
default `DJANGO_SECURE_SSL_REDIRECT=true` will redirect-loop until you
either add TLS (`docker-compose.tls.yml` is a ready-to-use overlay for
that) or explicitly opt out.

For a release image with `VERSION`/git commit/build date actually
baked into its OCI labels (see `docs/ARCHITECTURE.md` "Versioning"),
build with `scripts/build.sh` first, then start without `--build`:

```bash
./scripts/build.sh
docker compose -f docker-compose.yml up -d
```

Backups run automatically once a day out of the box
(`docker-compose.yml`'s `backup-scheduler` service) — see
[`docs/BACKUP.md`](docs/BACKUP.md) for the full picture, including the
admin-only web UI and host-side scripts for on-demand ones.

## Project history

`README.md` (this file) stays a short orientation. For the detailed
story of how each feature was actually built — including bugs found
and fixed along the way — see
[`docs/DEVELOPMENT_LOG.md`](docs/DEVELOPMENT_LOG.md). For a terse,
version-bucketed summary of what changed, see
[`CHANGELOG.md`](CHANGELOG.md).

## License

[GNU AGPLv3](LICENSE) — see [`LICENSE`](LICENSE) for the full text.
