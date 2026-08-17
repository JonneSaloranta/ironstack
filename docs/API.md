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
| `nutrition` | Foods, meal slots, recipes, diary entries, nutrition goals/targets (goals/targets read-only — see below) |

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
harmless, just inert. `nutrition` has the same shape for its two
historized sub-resources: nutrition goals and targets accept Create/
Update/Delete flags but have no route to reach them, for the same
reason `records` doesn't — a goal/target is only ever created or
superseded through `apps.nutrition.services.set_goal`/`set_target`
(append a new row, close the old one), and a raw API write could
silently corrupt that append-only history the way a hand-edited PR
could. See `docs/NUTRITION.md` "NutritionGoal"/"NutritionTarget".

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

- **Create** (`/api/keys/new/`): a name plus one Create/Read/Update/
  Delete row per context described above (`apps.api.forms.
  ApiKeyCreateForm` builds this from `ApiContext` itself, so it never
  needs updating by hand when a context is added). The full secret is
  shown exactly once immediately after — leaving that page (or
  refreshing it) means it's gone for good; a lost key means revoking
  it and creating a new one, the same as a lost password.
- **List** (`/api/keys/`): every key's name, short identifying prefix
  (e.g. `isk_a1b2c3d4…` — never the full secret), tier, and last-used
  time. A "?" button next to the page heading opens an in-app
  reference (base URL, the auth header, a curl and a Python example
  using this deployment's own real host, not a placeholder) —
  everything on this page condensed for someone who just wants to
  start calling the API without leaving the app to find this document.
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
| nutrition | `foods/`, `foods/<id>/`, `meal-slots/`, `meal-slots/<id>/`, `recipes/`, `recipes/<id>/`, `recipe-ingredients/`, `recipe-ingredients/<id>/`, `diary-entries/`, `diary-entries/<id>/`, `nutrition-goals/`, `nutrition-goals/<id>/` (read-only), `nutrition-targets/`, `nutrition-targets/<id>/` (read-only) |

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

Nutrition follows the same shapes: `foods/`/`meal-slots/` are
soft-deletable, shared-or-own resources exactly like `measurement-
types/`/`activity-types/` — a food or meal slot with no `owner` is
visible to (and loggable by) every user but only ever editable by
whoever created a *custom* one. `recipes/` is a real, permanent delete
like `programs/` (a recipe has no `active` field either). A
`diary-entries/` row must have exactly one of `food`/`recipe` set,
never both or neither — the same `CheckConstraint` the model itself
enforces, checked again in the serializer so a bad request gets a
plain `400` with a message instead of a raw database error. A
`recipe-ingredients/`/`diary-entries/` write is only accepted if the
`food`/`recipe`/`meal_slot` it points at is one this key's user can
actually see — the same visibility rule `apps.nutrition.services.
search_foods`/`visible_meal_slots` already enforce for the web UI,
re-checked here since a `PrimaryKeyRelatedField` has no ownership
concept of its own (same pattern `WorkoutSerializer.validate_program`
and `BodyMeasurementSerializer.validate_measurement_type` already use
elsewhere in this file). Importing a food from OpenFoodFacts by
barcode isn't exposed here — a client creates a food the same way the
web form does, by supplying its own values directly; see "What's
deliberately not here" below.

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
- **Importing a food from OpenFoodFacts by barcode.** The web UI's
  search-and-import flow (`docs/NUTRITION.md` "OpenFoodFacts
  integration") isn't mirrored here yet — `POST foods/` creates a
  plain hand-entered food (matching `FoodForm`), the same shape a
  user typing their own values in gets. Deliberately deferred rather
  than half-built alongside everything else in this pass.

None of these are ruled out for later, they just aren't part of this
first pass — see the phased-implementation principle in `CLAUDE.md`.
