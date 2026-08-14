# API

A real, programmatic API — added on explicit request (see the git log
around `apps/api`), which is why it exists at all: `docs/ARCHITECTURE.md`
"API layer" originally said not to build one "until an actual client
needs it," and Django REST Framework specifically was called out as a
dependency not to add without that need. Both are superseded by this
document for anything API-related; `ARCHITECTURE.md` still explains why
domain logic staying HTTP-agnostic (`apps/*/services.py`) made this
possible to add later without touching a single service function.

Implemented with [Django REST Framework](https://www.django-rest-framework.org/)
rather than hand-rolled — the combination of per-key CRUD permissions,
serialization, and rate-limit throttling this needed is exactly DRF's
job, and hand-rolling equivalents would just be a worse-tested version
of what it already does.

## Authentication

`Authorization: Bearer <key>` on every request. There is no other way
in — no session/cookie auth, no Basic auth. This is deliberately a
separate front door from the server-rendered UI (which stays
session-authenticated as before): a machine client always identifies
itself with its own key, never by "being logged in" the way a browser
tab is.

```
curl -H "Authorization: Bearer isk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
     https://your-instance/api/v1/profile/
```

A key's secret is shown exactly once, at creation (`/api/keys/new/` —
see "Managing keys" below) — only its SHA-256 hash is ever stored
(`apps.api.models.ApiKey.key_hash`), the same reasoning as a password
hash: a database leak alone can't be used to authenticate. Unlike a
password, a key is high-entropy and machine-generated, so it's hashed
with plain SHA-256 rather than a slow, salted password hasher — see
`apps.api.crypto`'s own docstring for why that's the correct choice
here, not a shortcut.

## Contexts and permissions

Every endpoint belongs to exactly one **context**
(`apps.api.models.ApiContext`):

| Context | Covers |
|---|---|
| `profile` | The authenticated user's own account/preferences |
| `exercises` | Exercises, muscle groups, equipment |
| `programs` | Programs, workouts, exercise prescriptions |
| `workouts` | Workout sessions, performed exercises, logged sets |
| `measurements` | Measurement types, body measurement readings |
| `activities` | Activity types, logged activities |
| `records` | Personal records (read-only — see below) |
| `analytics` | Training summaries, achievements (read-only) |

Each API key carries, per context, four independent flags — **Create**,
**Read**, **Update**, **Delete** — checked fresh on every request
(`apps.api.permissions.HasContextPermission`) against the HTTP method:
`GET`/`HEAD`/`OPTIONS` need Read, `POST` needs Create, `PUT`/`PATCH`
need Update, `DELETE` needs Delete. A key with `programs: {read}` and
nothing else can browse programs but can't touch exercises at all, and
can't create/edit/delete programs either — permissions are genuinely
independent per verb, not a single "allowed" toggle.

`records` and `profile` accept Create/Delete flags for a uniform model,
but neither context has a route that verb could ever reach — records
are derived, never directly writable (see `docs/PR_SYSTEM.md`), and an
account isn't created/deleted through this API. Granting those flags is
harmless, just inert.

## Rate limits and tiers

Every key belongs to a `RateLimitTier` (`apps.api.models.RateLimitTier`)
— a named pair of `requests_per_minute`/`requests_per_day` limits, both
enforced (`apps.api.throttling`). New keys get whichever tier is
flagged `is_default` (seeded: **Basic** 30/min·2,000/day, **Standard**
100/min·10,000/day — default — **Extended** 300/min·50,000/day).

**These are admin-editable at runtime** — an admin edits a tier's numbers
in Django admin, and every key on that tier is affected on its very
next request, no redeploy, no cache to invalidate (see
`apps.api.throttling`'s own docstring: the rate is read from the
tier fresh on every request). An admin can also add new tiers or
reassign an individual key to a different one from the `ApiKey` admin
page.

Exceeding a limit returns `429 Too Many Requests`.

Rate-limit counters live in the database (`django_cache` table,
`CACHES["default"]` in `config/settings/base.py` — Django's own
`DatabaseCache` backend), not Django's default in-memory cache: gunicorn
runs multiple worker *processes* (`docker-compose.yml`, `--workers 3`)
with no shared memory between them, so an in-memory counter would give
each worker its own independent count — a key's real allowed throughput
would end up `worker_count` times its configured tier, silently. The
database backend needs no new infrastructure (no Redis) and is created
by `manage.py createcachetable`, wired into both compose files'
startup command right after `migrate`.

## Canonical units, always

Every weight in every API response/request is canonical **kilograms**;
every length is canonical **meters**. This never converts to a user's
`unit_system` preference the way the server-rendered UI does — a
machine caller needs one unambiguous unit it can rely on regardless of
who's asking, not a human-facing display choice. `unit_system` itself
is still exposed on `/api/v1/profile/` as a plain preference value (so
a client can build its *own* imperial/metric UI on top if it wants to),
it just never changes what any other endpoint's numbers mean.

Datetimes are ISO 8601, always UTC on the wire (`USE_TZ=True`) —
converting to a user's `timezone` preference for *display* is a client
concern, the same reasoning as units.

## Managing keys

Self-service, from the Profile page → "API keys" (`/api/keys/` —
session-authenticated, ordinary server-rendered pages, not part of the
API itself):

- **Create** (`/api/keys/new/`): a name plus the 8×4 permission grid
  described above. The full secret is shown exactly once immediately
  after — leaving that page (or refreshing it) means it's gone for
  good; a lost key means revoking it and creating a new one, the same
  as a lost password.
- **List** (`/api/keys/`): every key's name, short identifying prefix
  (e.g. `isk_a1b2c3d4…` — never the full secret), tier, and last-used
  time.
- **Revoke** (a button on each key's row): permanently deletes it —
  unlike almost everything else in this app, a key is a credential, not
  training history, so there's no soft-delete/audit-trail reason to
  keep a revoked one around (see `apps.api.services.revoke_api_key`'s
  own docstring).

A user may have at most `ApiSettings.max_api_keys_per_user` keys at
once (seeded default: **10**) — also admin-editable at runtime, from
the `ApiSettings` singleton in Django admin.

## Endpoints

All under `/api/v1/`. List/create endpoints are paginated (25 per page,
`?page=N`) except where noted.

| Context | Endpoints |
|---|---|
| profile | `GET`/`PATCH` `profile/` (singleton — no id) |
| exercises | `exercises/`, `exercises/<id>/`, `muscle-groups/` (read-only), `equipment/` (read-only) |
| programs | `programs/`, `programs/<id>/`, `workouts/`, `workouts/<id>/`, `prescriptions/`, `prescriptions/<id>/` |
| workouts | `sessions/`, `sessions/<id>/` (create = start; `PATCH status` to `completed`/`abandoned` = end), `performed-exercises/` (create + read only), `sets/`, `sets/<id>/` |
| measurements | `measurement-types/`, `measurement-types/<id>/`, `measurements/`, `measurements/<id>/` |
| activities | `activity-types/`, `activity-types/<id>/`, `activities/`, `activities/<id>/` |
| records | `records/`, `records/<id>/` (read-only) |
| analytics | `analytics/summary/?range=7d\|30d\|all` (default 30d), `analytics/achievements/` (both read-only) |

Every endpoint goes through the exact same domain service functions the
server-rendered web views already use (`apps/exercises/services.py`,
`apps/workouts/services.py`, `apps/records/services.py`, ...) — never a
second copy of ownership-scoping, snapshot-on-start, or PR-detection
logic. Concretely: `POST sets/` calls
`apps.workouts.services.log_set` and then
`apps.records.services.check_and_record_prs`, exactly the two calls the
web UI's own set-logging view makes — a set logged through the API sets
PRs the same as one logged through the app.

Ownership rules match the web UI exactly too: a system exercise/program
template is readable by everyone but only ever editable by whoever
created it (or not at all, for a system template); a request against
someone else's row 404s, the same as the equivalent web page would
(`apps.api.viewsets.OwnedResourceViewSet` — the request never even
learns the row exists, rather than a 403 confirming it does). Deleting
an exercise/measurement type/activity type soft-deletes it (matching
`active=False`, never a hard delete — see `docs/DOMAIN_MODEL.md`);
deleting a program is a real, permanent delete (it has no `active`
field, matching its own web view: "Delete this program? This cannot be
undone.").

## What's deliberately not here

- **Bulk/batch endpoints.** Every write is one row at a time, matching
  the granularity the web UI itself logs at.
- **Webhooks / push notifications.** Nothing calls out to a client;
  everything here is request/response.
- **OAuth / third-party delegated auth.** A key is issued directly by
  the account it belongs to, for that account only — there's no
  "connect your account to another service" flow, since this is a
  self-hosted, typically single-household instance, not a multi-tenant
  SaaS with external integrators.

None of these are ruled out for later, they just aren't part of this
first pass — see the phased-implementation principle in `CLAUDE.md`.
