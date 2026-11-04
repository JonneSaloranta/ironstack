# API

A real, programmatic API - added on explicit request (see the git log
around `apps/api`), which is why it exists at all: `docs/ARCHITECTURE.md`
"API layer" originally said not to build one "until an actual client
needs it," and Django REST Framework specifically was called out as a
dependency not to add without that need. Both are superseded by this
document for anything API-related; `ARCHITECTURE.md` still explains why
domain logic staying HTTP-agnostic (`apps/*/services.py`) made this
possible to add later without touching a single service function.

Implemented with [Django REST Framework](https://www.django-rest-framework.org/)
rather than hand-rolled - the combination of per-key CRUD permissions,
serialization, and rate-limit throttling this needed is exactly DRF's
job, and hand-rolling equivalents would just be a worse-tested version
of what it already does.

## Authentication

`Authorization: Bearer <key>` on every request. There is no other way
in - no session/cookie auth, no Basic auth. This is deliberately a
separate front door from the server-rendered UI (which stays
session-authenticated as before): a machine client always identifies
itself with its own key, never by "being logged in" the way a browser
tab is.

```
curl -H "Authorization: Bearer isk_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
     https://your-instance/api/v1/profile/
```

A key's secret is shown exactly once, at creation (`/api/keys/new/` -
see "Managing keys" below) - only its SHA-256 hash is ever stored
(`apps.api.models.ApiKey.key_hash`), the same reasoning as a password
hash: a database leak alone can't be used to authenticate. Unlike a
password, a key is high-entropy and machine-generated, so it's hashed
with plain SHA-256 rather than a slow, salted password hasher - see
`apps.api.crypto`'s own docstring for why that's the correct choice
here, not a shortcut.

## Contexts and permissions

Every endpoint belongs to exactly one **context**
(`apps.api.models.ApiContext`):

| Context | Covers |
|---|---|
| `profile` | The authenticated user's own account/preferences, guided tours |
| `exercises` | Exercises, muscle groups, equipment |
| `programs` | Programs, workouts, exercise prescriptions |
| `workouts` | Workout sessions, performed exercises, logged sets |
| `measurements` | Measurement types, body measurement readings |
| `activities` | Activity types, logged activities |
| `records` | Personal records (read-only - see below) |
| `analytics` | Training summaries and charts, achievements, recently active users (read-only) |
| `nutrition` | Foods, meal slots, recipes, diary entries, nutrition goals/targets (never edited - a new goal goes through the same service as the web), a nutrition profile, diet plans (generation/activation/apply are service-backed, not raw writes; a plan's meals/items are read-only - see below) |
| `stretching` | Stretches, stretch routines and their items, stretching sessions (a quick log or a guided session) |
| `social` | Friends, friend requests, blocks, direct messages, groups and their invites and messages |
| `coaching` | Coaching requests and relationships, a trainer's clients and shared plans, importing and updating plans a coach shared |
| `assistant` | The AI assistant: turning it on and off, its settings, conversations and their replies, accepting or dismissing its proposals |

Each API key carries, per context, four independent flags - **Create**,
**Read**, **Update**, **Delete** - checked fresh on every request
(`apps.api.permissions.HasContextPermission`) against the HTTP method:
`GET`/`HEAD`/`OPTIONS` need Read, `POST` needs Create, `PUT`/`PATCH`
need Update, `DELETE` needs Delete. A key with `programs: {read}` and
nothing else can browse programs but can't touch exercises at all, and
can't create/edit/delete programs either - permissions are genuinely
independent per verb, not a single "allowed" toggle.

`analytics/achievements/` is the one endpoint in this whole API that
isn't scoped to the requesting user's own data - every other endpoint
(including `analytics/summary/` right above it) only ever returns or
accepts *your own* rows, `records`/`can_read` and all. Achievements are
deliberately different, and match `templates/core/dashboard.html`'s own
achievements carousel exactly: an all-time, shared-across-every-user
list of highlights, not a personal one - see `docs/UI.md` "Achievements
carousel" for the full reasoning (a self-hosted instance is typically
one household or small gym, where seeing a housemate's new streak is
the point). `User.show_achievements=False` excludes that user's own
figures from the result entirely, for every caller, themselves
included - the same opt-out the dashboard itself respects, not
something this API route bypasses or has its own separate copy of.

`records` and `profile` accept Create/Delete flags for a uniform model,
but neither context has a route that verb could ever reach - records
are derived, never directly writable (see `docs/PR_SYSTEM.md`), and an
account isn't created/deleted through this API. Granting those flags is
harmless, just inert. `nutrition` has the same shape for its two
historized sub-resources: nutrition goals and targets accept Create/
Update/Delete flags but have no route to reach them, for the same
reason `records` doesn't - a goal/target is only ever created or
superseded through `apps.nutrition.services.set_goal`/`set_target`
(append a new row, close the old one), and a raw API write could
silently corrupt that append-only history the way a hand-edited PR
could. See `docs/NUTRITION.md` "NutritionGoal"/"NutritionTarget". A
diet plan's meals/items have the same "no route to reach a raw write"
shape too, for the reason given under "Endpoints" below - only a
plan's `name` and its service-backed actions are actually writable.

## Rate limits and tiers

Every key belongs to a `RateLimitTier` (`apps.api.models.RateLimitTier`)
- a named pair of `requests_per_minute`/`requests_per_day` limits, both
enforced (`apps.api.throttling`). New keys get whichever tier is
flagged `is_default` (seeded: **Basic** 30/min·2,000/day, **Standard**
100/min·10,000/day - default - **Extended** 300/min·50,000/day).

**These are admin-editable at runtime** - an admin edits a tier's numbers
in Django admin, and every key on that tier is affected on its very
next request, no redeploy, no cache to invalidate (see
`apps.api.throttling`'s own docstring: the rate is read from the
tier fresh on every request). An admin can also add new tiers or
reassign an individual key to a different one from the `ApiKey` admin
page.

Exceeding a limit returns `429 Too Many Requests`.

Rate-limit counters live in the database (`django_cache` table,
`CACHES["default"]` in `config/settings/base.py` - Django's own
`DatabaseCache` backend), not Django's default in-memory cache: gunicorn
runs multiple worker *processes* (`docker-compose.yml`, `--workers 3`)
with no shared memory between them, so an in-memory counter would give
each worker its own independent count - a key's real allowed throughput
would end up `worker_count` times its configured tier, silently. The
database backend needs no new infrastructure (no Redis) and is created
by `manage.py createcachetable`, wired into both compose files'
startup command right after `migrate`.

## Canonical units, always

Every weight in every API response/request is canonical **kilograms**;
every length is canonical **meters**. This never converts to a user's
`unit_system` preference the way the server-rendered UI does - a
machine caller needs one unambiguous unit it can rely on regardless of
who's asking, not a human-facing display choice. `unit_system` itself
is still exposed on `/api/v1/profile/` as a plain preference value (so
a client can build its *own* imperial/metric UI on top if it wants to),
it just never changes what any other endpoint's numbers mean.

Datetimes are ISO 8601, always UTC on the wire (`USE_TZ=True`) -
converting to a user's `timezone` preference for *display* is a client
concern, the same reasoning as units.

## Display names

Built-in exercises, programs, workouts, muscle groups, equipment,
measurement and activity types, meal slots, stretches and stretch
routines are stored with English names and translated where the web
shows them. Their API rows keep the stored `name` (stable, what
`?search=` and imports match) and add a read-only `display_name`: the
same text the web shows, translated to the request's `Accept-Language`
when the server supports that language, else to the user's profile
`language`, else the site default. A name nobody translated - one a
user typed - comes back unchanged, so a client can always show
`display_name`.

The same goes for names nested in other rows and for the long texts,
each as a `display_*` twin next to the stored value: `exercise_display_name`
on prescriptions, performed exercises, records and a set's `new_records`;
`stretch_display_name` on performed stretches; `muscle_group_display_name`
in `analytics/muscle-groups/`; `display_name` on recipes and
`recipe_display_name` on diary entries; `display_instructions` on
exercises, stretches and recipes; and `display_description` on programs
and stretch routines. An empty text stays empty.

## Managing keys

Self-service, from the Profile page → "API keys" (`/api/keys/` -
session-authenticated, ordinary server-rendered pages, not part of the
API itself):

- **Create** (`/api/keys/new/`): a name plus one Create/Read/Update/
  Delete row per context described above (`apps.api.forms.
  ApiKeyCreateForm` builds this from `ApiContext` itself, so it never
  needs updating by hand when a context is added). The full secret is
  shown exactly once immediately after - leaving that page (or
  refreshing it) means it's gone for good; a lost key means revoking
  it and creating a new one, the same as a lost password.
- **List** (`/api/keys/`): every key's name, short identifying prefix
  (e.g. `isk_a1b2c3d4…` - never the full secret), tier, and last-used
  time. A "?" button next to the page heading opens an in-app
  reference (base URL, the auth header, a curl and a Python example
  using this deployment's own real host, not a placeholder) -
  everything on this page condensed for someone who just wants to
  start calling the API without leaving the app to find this document.
- **Revoke** (a button on each key's row): permanently deletes it -
  unlike almost everything else in this app, a key is a credential, not
  training history, so there's no soft-delete/audit-trail reason to
  keep a revoked one around (see `apps.api.services.revoke_api_key`'s
  own docstring).

A user may have at most `ApiSettings.max_api_keys_per_user` keys at
once (seeded default: **10**) - also admin-editable at runtime, from
the `ApiSettings` singleton in Django admin.

## Interactive docs

`/api/docs/` - a Swagger UI, generated straight from the same DRF
viewsets/serializers `/api/v1/` itself is built from
([drf-spectacular](https://github.com/tfranzel/drf-spectacular)), so
it can never drift out of sync with what the API actually does the
way a hand-written reference could. Every operation can be tried
directly from the page: click "Authorize", paste in one of your own
API keys (create one under "Managing keys" above first), and every
"Try it out" request from then on sends real requests against this
instance with `Authorization: Bearer <key>` attached automatically.

Session-authenticated (an ordinary logged-in page, like "Managing
keys" above) - a deliberately separate credential from the API key
you authorize with inside it. The raw OpenAPI 3 schema itself is at
`/api/docs/schema/` (also session-gated), for generating a typed
client or importing into another tool (Postman, Insomnia, ...) instead
of using the bundled UI - or use the committed
`docs/api/openapi-v1.yaml` (see "Versioning" below), which needs no
login.

Served entirely from this instance's own static files
(`drf-spectacular-sidecar`), not a CDN - works the same on an
offline/LAN-only deployment as on one with internet access, and needs
no exception to this app's CSP (`apps.core.middleware.
ContentSecurityPolicyMiddleware`, docs/SECURITY.md
"Content-Security-Policy").

## Versioning

The version is part of every URL: `/api/v1/...`. A client that works
against v1 today keeps working against v1 after any later release.

**What may change within a version - additions only:**

- a new endpoint, or a new method on an existing one
- a new field in a response
- a new optional query parameter, header or request field
- a new value in an enum the client only sends (never one it receives
  and has to handle - see below)

So a client must ignore response fields it doesn't know. Everything
else is a breaking change and needs a new version: removing or renaming
an endpoint, field or parameter, changing a field's type or meaning,
making something optional required, a new required request field, or a
new value in an enum the API returns.

**How it's enforced:**

- Each served version's OpenAPI schema is committed as
  `docs/api/openapi-<version>.yaml` (`apps.api.schema`). After an API
  change run `python manage.py api_schema` and commit the regenerated
  file - the diff is the change as a client sees it, docstrings
  included, since they're the Swagger descriptions.
  `python manage.py api_schema --check` (a CI step and a test) fails
  while the committed file doesn't match the code.
- CI's `api-compatibility` job compares each committed schema with
  master's copy using [oasdiff](https://github.com/oasdiff/oasdiff) and
  fails on every change it classifies as an error; publishing the image
  waits for it. Its warnings (e.g. a new enum value in a response) are
  printed but don't fail the run - read them in review against the
  rules above.

**Adding a version (v2):** only when a breaking change is genuinely
needed, and only for what it changes. The version is DRF's
`NamespaceVersioning`: the URL include's namespace (`config/urls.py`),
available in every API view and serializer as `request.version`. So:

1. Add `path("api/v2/", include("apps.api.urls", namespace="v2"))`
   to `config/urls.py`, `"v2"` to `REST_FRAMEWORK["ALLOWED_VERSIONS"]`
   and to `apps.api.schema.VERSIONS`, then run `api_schema` to commit
   `openapi-v2.yaml`.
2. Make the breaking change only where `request.version == "v2"`
   (e.g. `get_serializer_class` returning a v2 serializer) - every other
   endpoint is shared, and v1's committed schema must not change.
3. Point the docs views (`apps.api.views_docs.SchemaView.api_version`)
   at v2, note in CHANGELOG.md when v1 will be removed, and keep it
   working until then. Removing v1 is itself a breaking change, made
   in a release whose notes say so.

`reverse("api:...")` resolves to the most recently included version, so
code and tests that need a specific one pass `current_app="v1"`.

## Endpoints

All under `/api/v1/`. List/create endpoints are paginated (25 per page,
`?page=N`) except where noted; `?page_size=N` asks for up to 100 per
page (`apps.api.pagination.ApiPagination` - a larger value is clamped,
not refused).

| Context | Endpoints |
|---|---|
| profile | `GET`/`PATCH` `profile/` (singleton - no id; `username` and `email` are read-only - an API key must not be able to redirect password resets; `date_joined` is read-only too; `body_tracking_reminders_enabled` is the web's on/off setting and `needs_body_tracking_reminder` is the web dashboard's own rule - true after 14 days without a reading, counted from `date_joined` when there's none; `tutorials_enabled` switches automatic tours; `is_personal_trainer` and `accepting_new_clients` are the web's coaching switches - turning the first off doesn't end existing coaching relationships, the second only stops new requests), `tutorials/` (`GET` - every guided tour with its steps in the request's or user's language, `completed`, and the `web_url` it runs on), `tutorials/<key>/complete/`, `tutorials/<key>/reset/`, `tutorials/reset/` (all `POST`, `204`; completion is shared with the web) |
| exercises | `exercises/` (`?search=<text>` matches the name, also as translated to your language; `?ids=1,2,3` fetches a known set), `exercises/<id>/`, `muscle-groups/` (read-only), `equipment/` (read-only) |
| programs | `programs/`, `programs/<id>/`, `programs/<id>/copy/` (`POST` - your own editable copy of any visible program, workouts and prescriptions included, `201` with it), `workouts/`, `workouts/<id>/`, `prescriptions/`, `prescriptions/<id>/` (each prescription, nested or not, carries a read-only `exercise_name`), `up-next/` (`GET` - the dashboard's "Up next": `program`, `workout` and the `day` a weekly program schedules it for, all null before the user has completed a workout from a program; answers even while a session is in progress) |
| workouts | `exercises/<id>/last-performance/` (`GET` - the sets from the most recent completed session that included the exercise, warmups flagged; `session`/`started_at` null and `sets` empty if none), `sessions/`, `sessions/<id>/` (create = start; `PATCH status` to `completed`/`abandoned` = end; the list takes `?status=planned\|in_progress\|completed\|abandoned`, `?started_after=` (inclusive) and `?started_before=` (exclusive), each an ISO 8601 datetime or a date meaning its midnight in the server's time zone), `performed-exercises/` (create + read only), `performed-exercises/<id>/suggestion/` (`GET` - the smart weight suggestion the web shows when logging a set: `weight` in kg, `min_reps`/`max_reps`, the prescription's progression `method`, `action`, `confidence` and a plain-language `reason` in the user's language; every field null when no prescription is behind the exercise; only ever a default the user confirms), `sets/`, `sets/<id>/` (a create response also has `new_records`: the personal records the set broke, same shape as `records/` plus the `previous_value` it beat - empty when none) |
| measurements | `measurement-types/`, `measurement-types/<id>/`, `measurements/` (`?measurement_type=<id>`, `?recorded_after=` (inclusive) and `?recorded_before=` (exclusive), ISO 8601 datetimes or dates like `sessions/`), `measurements/<id>/` |
| activities | `activity-types/`, `activity-types/<id>/`, `activities/` (`?activity_type=<id>`, `?date_after=` (inclusive) and `?date_before=` (exclusive), `YYYY-MM-DD`), `activities/<id>/` |
| records | `records/`, `records/<id>/` (read-only, with `exercise_name`) |
| analytics | `analytics/summary/?range=7d\|30d\|all` (default 30d), `analytics/volume/?range=` (volume per ISO week), `analytics/muscle-groups/?range=` (volume per primary muscle group), `analytics/exercises/<id>/?range=` (sessions and volume in range, and the best set per training day by estimated 1RM), `analytics/recently-active/` (the dashboard's "Recently active" list: users who share their training, by when they last trained), `analytics/achievements/` (all read-only; `range` also takes `3m`, `6m` and `1y`; volumes and weights in canonical kg) |
| nutrition | `foods/` (`?search=<text>` matches the name or brand, a barcode matches `off_id`; `?ids=1,2,3`), `foods/<id>/`, `foods/import-off/` (`POST`), `foods/search-off/?q=` (`GET`), `meal-slots/`, `meal-slots/<id>/`, `recipes/`, `recipes/<id>/`, `recipe-ingredients/`, `recipe-ingredients/<id>/`, `diary-entries/` (`?date=YYYY-MM-DD` for one day, `?date_after=` (inclusive) / `?date_before=` (exclusive) for a range; each entry has a read-only `nutrition` object for its quantity, from the values snapshotted when it was logged, and `food_name`/`recipe_name`, so entries of a since-deleted recipe still show and count), `diary-entries/<id>/`, `diary-entries/copy-day/` (`POST` `{source_date, target_date}` - the web's "Copy day", answers `{copied}`), `nutrition/daily-totals/?date=` (`GET` - the day's diary added up, default today), `nutrition-goals/` (`POST` starts a new goal with a calculated target), `nutrition-goals/<id>/` (read-only), `nutrition-targets/`, `nutrition-targets/<id>/` (read-only), `nutrition/adjustment-suggestion/` (`GET`), `nutrition/adjustment-suggestion/accept/` (`POST`), `nutrition/maintenance/` (`GET` - the dashboard's "Observed maintenance" card: `calories` and `confidence`, or null with a `reason` saying what's missing, plus the figures behind it - `days_logged`, `mean_calories`, `possible_logging_gaps`, `weight_rate_kg_per_week`, `weight_days`, `span_days` - each null until the estimate got that far), `nutrition/stats/?range=7d\|14d\|30d\|3m\|ytd\|1y\|all` (`GET`, default `30d` - the statistics page: the chosen `period`'s days logged, mean/median calories, median macros and `possible_logging_gaps`, every period ending yesterday; `daily` calories from its start through today for the chart, null on an unlogged day; `target_calories`; and every fixed period in `periods`), `nutrition/profile/` (singleton - no id; `PUT` creates it), `diet-plans/`, `diet-plans/<id>/`, `diet-plans/<id>/activate/`, `diet-plans/<id>/deactivate/`, `diet-plans/<id>/apply/` (all `POST`), `diet-plans/<id>/shopping-list/` (`GET`), `diet-plans/<id>/shopping-settings/` (`GET`/`PUT`/`PATCH`), `diet-plan-meals/`, `diet-plan-meals/<id>/` (read-only), `diet-plan-items/`, `diet-plan-items/<id>/` (read-only) |
| stretching | `stretches/`, `stretches/<id>/`, `stretch-routines/`, `stretch-routines/<id>/` (items nested read-only), `stretch-routines/<id>/copy/` (`POST` - your own editable copy, items included), `stretch-routine-items/`, `stretch-routine-items/<id>/` (own routines only), `stretch-sessions/` (`?after_workout=<session id>` - only the cool-downs done after that workout), `stretch-sessions/<id>/` (create = quick log with `duration`, optionally `after_workout` - one of your finished workout sessions - to mark it as its cool-down; name/routine/status, `after_workout` and the per-stretch snapshot are read-only afterwards), `stretch-sessions/start/` (`POST` `{routine}` or `{stretches: [ids], name?}`, plus `after_workout?` - a guided session, its plan snapshotted like the web player; `409` with the in-progress session's `id` when one is already running), `stretch-sessions/<id>/mark/` (`POST` `{performed_stretch, done, actual_seconds?}` - one stretch done or skipped), `stretch-sessions/<id>/complete/`, `stretch-sessions/<id>/abandon/` (`POST`; `409` unless in progress), `sessions/<id>/cooldown/` (`GET` - the web's cool-down card for one finished workout: the trained primary `muscle_groups`, then a `routine` covering them or up to six library `stretches` (both empty when nothing matches), `planned_seconds`, and `done` - the cool-down already started or done after it, or null; `409` unless the workout is completed; the key needs both workouts and stretching read) |
| social | `social/friends/` (`GET`), `social/friends/<user_id>/` (`DELETE` - unfriend), `social/friends/<user_id>/mute/` / `unmute/` (`POST` - push notifications only), `social/users/?search=` (`GET` - people to befriend by username, max 20, never friends or anyone blocked either way), `social/friend-requests/` (`GET` pending to and from you; `POST {user}` sends one, or accepts theirs if they asked first), `social/friend-requests/<id>/accept/` / `decline/` (`POST`), `social/blocks/` (`GET`; `POST {user}` blocks: ends the friendship and pending requests), `social/blocks/<user_id>/` (`DELETE` - unblock), `social/conversations/` (`GET` - a thread per friend and group with unread counts, and pending request/invite counts), `social/messages/<user_id>/` (`GET` the thread with a friend, last 100 oldest first, `?after=<message id>` for newer ones only, marks it read; `POST {body}` sends), `social/groups/` (`GET` yours; `POST {name, description}` creates one you own), `social/groups/join/` (`POST {code}` - an invite code), `social/groups/<id>/` (members only; `PATCH` owner/admin, `DELETE` owner; `invite_code` shown to owner/admin only), `social/groups/<id>/members/` (`GET`), `social/groups/<id>/members/<user_id>/` (`DELETE` removes - owner/admin; `PATCH {role: admin\|member}` - owner), `social/groups/<id>/transfer/` (`POST {user}` - owner), `social/groups/<id>/leave/`, `social/groups/<id>/mute/` (`POST {muted}`), `social/groups/<id>/invite-link/` (`POST {enabled?, regenerate?}` - owner/admin), `social/groups/<id>/invites/` (`POST {user}` - invite a friend), `social/groups/<id>/messages/` (like direct messages), `social/group-invites/` (`GET` pending to you), `social/group-invites/<id>/accept/` / `decline/` (`POST`) - every rule is apps.social.services' own, a broken one answers `400` with its message in your language; other users appear by `id` and `username` only |
| coaching | `coaching/requests/` (`GET` pending ones you sent or received; `POST {username}` asks a trainer to coach you), `coaching/requests/<id>/accept/` / `decline/` (`POST` - the trainer), `coaching/coaches/` (`GET` - your active coaches), `coaching/clients/` (`GET` - a trainer's active clients), `coaching/clients/<username>/` (`GET` - one active client: 30-day training summary, highlights, latest records, latest body measurements, imported shared programs; `404` for anyone else), `coaching/relationships/<id>/end/` (`POST` - either side), `coaching/shared/` (`GET` - plans your coaches shared with you, `imported` when you have a copy), `coaching/shared/programs/<id>/import/`, `coaching/shared/diet-plans/<id>/import/` (`POST`, `201` with your copy), `coaching/programs/<id>/update/`, `coaching/diet-plans/<id>/update/` (on your imported copy: `GET` `{available, diff}`; `POST` replaces its contents with the coach's version, `409` when already up to date), `coaching/programs/<id>/clients/`, `coaching/diet-plans/<id>/clients/` (`GET`/`PUT {clients: [user ids]}` - a trainer's own plan, shared only with active clients; `403` for a non-trainer) - every rule is apps.coaching.services' own, a broken one answers `400` with its message in your language |
| assistant | `assistant/` (`GET` - on/available, provider and model, today's allowance on the instance's provider), `assistant/enable/` / `disable/` (`POST` - enabling is the user's consent to send their data to the model: show the explanation from the web's assistant settings first), `assistant/settings/` (`GET`/`PATCH` - `reads_workouts`, `reads_body`, `reads_food`, `reads_activities`, `reads_stretching`, `reads_profile`, `about_me`, `own_key_model`, `own_key_effort`; the user's own Anthropic key is set on the web only, as a credential), `assistant/conversations/` (`GET` latest 50; `POST {text}` starts one, `201`), `assistant/conversations/<id>/` (`GET` with messages and proposals; `DELETE`), `assistant/conversations/<id>/messages/` (`POST {text}` - `201` with the reply bubble, `pending` until the assistant worker writes it; `400` while a reply is still being written), `assistant/messages/<id>/` (`GET` - poll a reply: `pending` -> `running` -> `done`/`error`), `assistant/proposals/<id>/accept/` (`POST` - `201` with the `kind` and `created_id` of the new inactive diet plan or program, or the new recipe; `409` when it can't be created as proposed anymore), `assistant/proposals/<id>/dismiss/` (`POST`) |

Every endpoint goes through the exact same domain service functions the
server-rendered web views already use (`apps/exercises/services.py`,
`apps/workouts/services.py`, `apps/records/services.py`, ...) - never a
second copy of ownership-scoping, snapshot-on-start, or PR-detection
logic. Concretely: `POST sets/` calls
`apps.workouts.services.log_set` and then
`apps.records.services.check_and_record_prs`, exactly the two calls the
web UI's own set-logging view makes - a set logged through the API sets
PRs the same as one logged through the app. Likewise `PATCH`/`DELETE
sets/<id>/` and `DELETE sessions/<id>/` call
`apps.records.services.rebuild_records`, the same as the web's edit and
delete views, so records follow a corrected or deleted set
(`docs/PR_SYSTEM.md` "Correcting a logged set").

Ownership rules match the web UI exactly too: a system exercise/program
template is readable by everyone but only ever editable by whoever
created it (or not at all, for a system template); a request against
someone else's row 404s, the same as the equivalent web page would
(`apps.api.viewsets.OwnedResourceViewSet` - the request never even
learns the row exists, rather than a 403 confirming it does). Deleting
an exercise/measurement type/activity type soft-deletes it (matching
`active=False`, never a hard delete - see `docs/DOMAIN_MODEL.md`);
deleting a program is a real, permanent delete (it has no `active`
field, matching its own web view: "Delete this program? This cannot be
undone.").

Nutrition follows the same shapes: `foods/`/`meal-slots/` are
soft-deletable, shared-or-own resources exactly like `measurement-
types/`/`activity-types/` - a food or meal slot with no `owner` is
visible to (and loggable by) every user but only ever editable by
whoever created a *custom* one. `recipes/` lists the user's own recipes
and the built-in template ones (`owner` null, the ones the diet plan
wizard fills plans with) - both readable, with their ingredients, and
both loggable to the diary, but only the user's own are writable.
Deleting a recipe still used by diary entries or diet plans only hides
it, like the web; an unused one is deleted for good. A
`diary-entries/` row must have exactly one of `food`/`recipe`/quick
macros set (an explicit `null` for the other two is fine) - the same `CheckConstraint` the model itself
enforces, checked again in the serializer so a bad request gets a
plain `400` with a message instead of a raw database error. A
`recipe-ingredients/`/`diary-entries/` write is only accepted if the
`food`/`recipe`/`meal_slot` it points at is one this key's user can
actually see - the same visibility rule `apps.nutrition.services.
search_foods`/`visible_meal_slots` already enforce for the web UI,
re-checked here since a `PrimaryKeyRelatedField` has no ownership
concept of its own (same pattern `WorkoutSerializer.validate_program`
and `BodyMeasurementSerializer.validate_measurement_type` already use
elsewhere in this file). `POST foods/` creates a plain hand-entered
food, like the web form. An Open Food Facts product is saved with
`POST foods/import-off/` `{"barcode": "..."}` instead, through the same
service as the web (`apps.nutrition.services.import_or_refresh_food_
from_off`): a shared food with its Nutri-Score, NOVA group and OFF link,
`200` with the existing food when the barcode is already known (`201`
when new), `404` when OFF has no usable product, `429` when the
server's OFF budget is spent and `503` when the integration is off.
`GET foods/search-off/?q=` searches the visible foods and OFF together
(`status`, `local` foods, OFF `results` not imported yet) through the
server's cache and rate budget, so call it once per user action, not
while typing.

`nutrition/profile/` is a singleton like `profile/` itself - every
field of `apps.nutrition.models.NutritionProfile` is plainly
readable/writable, since (unlike a goal or target) these are inputs a
user corrects in place, not decisions worth preserving as history. A
user with no profile yet gets a plain `404`, not a `500`, and a `PUT`
creates it (`201`).

Goals and targets are history: never edited or deleted. Setting up
nutrition through the API takes the web onboarding's steps: `PUT
nutrition/profile/`, `height` on `profile/`, a body weight on
`measurements/`, then `POST nutrition-goals/` `{goal_type,
target_rate_kg_per_week, target_weight?, notes?}` - the same
`apps.nutrition.services.change_goal` the web's goal page uses, so the
open goal and target are ended and a target is calculated from the
profile, height and latest weight. The response is the goal with its
`target`; a `400` says what's missing, or that the rate's sign doesn't
fit the goal. `nutrition/adjustment-suggestion/` is the dashboard's
calorie adjustment card (rates in kg/week, `reason` in the user's
language); `POST .../accept/` works it out again and, when its
`action` is `adjust`, replaces the target with an `adjusted` one
(`201`), otherwise `409` with the current suggestion. Nothing is ever
adjusted without that explicit call.

`diet-plans/` generates through
`apps.nutrition.diet_builder.build_diet_plan` on `POST` rather than
accepting a raw row: the request body supplies `name`, `goal`
(optional - must be one of this key's own user's goals), the four
`target_*` numbers, `is_weekly` (optional), and `meal_slots` (a list
of meal slot ids to build around) - the response's nested `meals`
(each nesting `items`) is whatever the builder actually generated, not
an echo of the request. `PATCH` only accepts `name`; `is_active` and
every `target_*` field are read-only, since the "exactly one active
plan per user" invariant and a plan's snapshotted targets are only
ever supposed to change through the actions below, never a raw write.
`activate/`/`deactivate/`/`apply/` (all `POST`, no body except
`apply/`'s `{"date": "YYYY-MM-DD"}`) call
`apps.nutrition.services.set_active_diet_plan`/`deactivate_diet_plan`/
`apps.nutrition.diet_builder.apply_diet_plan` - `apply/` is the one
genuinely useful action a client would otherwise have to reconstruct
by hand (fetch every item, `POST` each one to `diary-entries/`
individually, re-deriving quantities and meal slots the plan already
knows) and returns the real `DiaryEntry` rows it created. `DELETE`
cascades to a plan's own meals/items, same as any other delete.
`diet-plan-meals/`/`diet-plan-items/` exist as their own top-level,
read-only endpoints too (same double-exposure `recipes/`/
`recipe-ingredients/` already has) - but unlike a recipe's ingredient
list, a plan's meals/items only ever come from the builder's own
calorie-splitting algorithm, so nothing here accepts a write; doing so
would silently break the calorie/macro balance the plan was built to
hit.

`diet-plans/<id>/shopping-list/` (`GET`) is the plan's shopping list for
one trip (docs/NUTRITION.md "Shopping list"), derived from the plan on
every request - nothing is stored. `?trip=<weekday>` (0=Monday) picks the
trip starting that shopping day (`400` if it isn't one); without it, the
trip shopped today or next. The response has the `trip` (`weekday` -
`null` when the plan has no shopping days, i.e. one list for the whole
week - and the `days` it buys for), every `trips` entry, the plan's
`include_shopping_day`, and `items`: one per food, recipes broken into
their ingredients, with `food`, `food_name`, `unit` (`g`/`ml`/`piece`),
`total` and `by_day` (`[{weekday, quantity}]` in the trip's day order).
`diet-plans/<id>/shopping-settings/` reads or writes the plan's
`weekdays` (list of 0–6) and `include_shopping_day`; `PUT` replaces both,
`PATCH` changes whichever is given. Both need the `nutrition` context -
read for `GET`, update for `PUT`/`PATCH`.

## Retrying writes

A client on a bad connection retries a write until it gets an answer -
but if the first attempt reached the server and only the response was
lost, the retry would log the same set twice. `POST sets/`,
`sessions/`, `performed-exercises/`, `diary-entries/`, `measurements/`,
`activities/` and `stretch-sessions/` therefore accept an
optional `Idempotency-Key` header (1-255 characters; one random value,
e.g. a UUID, per queued write, sent unchanged with every retry of it).

- The first request with a key runs normally; a successful (2xx)
  response is stored for that key and user (`apps.api.models.
  IdempotencyKey`, `apps.api.idempotency`).
- A repeat with the same key and the same method, path and body gets
  that stored response back, with an `Idempotent-Replayed: true` header,
  and creates nothing. Two requests with the same key at the same
  moment still create only one row.
- The same key with a different request is refused with `422`.
- A failed request (4xx/5xx) isn't stored, so retrying it with the same
  key simply runs it again.
- Keys are remembered for 24 hours, then forgotten. Without the header a
  request behaves exactly as before.

## What's deliberately not here

- **Bulk/batch endpoints.** Every write is one row at a time, matching
  the granularity the web UI itself logs at.
- **Webhooks / push notifications.** Nothing calls out to a client;
  everything here is request/response.
- **OAuth / third-party delegated auth.** A key is issued directly by
  the account it belongs to, for that account only - there's no
  "connect your account to another service" flow, since this is a
  self-hosted, typically single-household instance, not a multi-tenant
  SaaS with external integrators.

None of these are ruled out for later, they just aren't part of this
first pass - see the phased-implementation principle in `CLAUDE.md`.
