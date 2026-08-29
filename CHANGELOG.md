# Changelog

All notable changes to IronStack are recorded here, in the style of
[Keep a Changelog](https://keepachangelog.com/). The running version
lives in the repo-root `VERSION` file, not this document — see
`docs/ARCHITECTURE.md` "Versioning" for how that value gets baked into
a build and surfaced in the app (profile page footer, `version_info`
management command).

This file exists to answer "what changed between two versions", not to
duplicate the detailed, ongoing build log — that's
`docs/DEVELOPMENT_LOG.md`, which is updated with every feature as it
lands and remains the authoritative history. Each GitHub Release's own
notes are a separate, auto-generated list of every commit since the
previous release (`docs/ARCHITECTURE.md` "Versioning") — a terse,
complete audit trail, not a replacement for this file's shorter,
narrated summary of the same version.

## [1.8.0] — 2026-08-29

### Added
- Install-to-home-screen recommendation, shown once and dismissible
  (close, "Remind me later", or "Don't remind me"): uses the browser's
  own install prompt where one exists (Chrome/Edge/most Android
  browsers), and falls back to on-screen instructions on iOS Safari,
  which has no such prompt at all.

## [1.7.0] — 2026-08-28

### Added
- Opt-in Web Push notifications (Profile → Notifications) for direct
  and group messages, delivered even when the app isn't open —
  entirely optional, no cost unless `VAPID_*` env vars are configured
  (`docs/SECURITY.md` "Web Push notifications"). A floating
  unread-messages button now also appears on every page, the same
  "reachable from anywhere" treatment a workout in progress already
  gets.
- Mute a friend or a group (Friends & groups) to stop push
  notifications from just that one, without disabling notifications
  entirely — private to you, doesn't affect the friendship, group
  membership, or the messages themselves.
- Camera barcode scanning for food search/import now works in every
  browser with a camera, not just Chromium-based ones — a vendored
  fallback decoder (ZXing) loads automatically, only for browsers
  without the native `BarcodeDetector` API.

## [1.6.0] — 2026-08-28

### Added
- Friends, groups, and messaging (`apps/social`, `docs/SOCIAL.md`):
  send/accept friend requests, create groups with owner, admin, and
  member roles, invite someone directly or share a short, unguessable
  invite link (`/group/invite/<code>/`), message a friend or a group
  directly, and block another user. Two new opt-out privacy settings
  (allow friend requests, allow group invites) are asked once during
  onboarding and editable anytime from Profile. Reachable from a new
  "Friends & groups" card on the profile page, with a small dot badge
  on the Profile nav icon whenever something's waiting on you (a
  request, an invite, an unread message). The two privacy settings are
  also readable/writable from the public API (`GET`/`PATCH`
  `/api/v1/profile/`), alongside the other display preferences already
  exposed there.
- The public API now covers the two `apps.nutrition` resources that
  were still missing after the rest of nutrition's API surface
  shipped: a `nutrition/profile/` singleton (matching `profile/`
  itself) and full `diet-plans/` support (create/rename/delete,
  `activate`/`deactivate`/`apply` actions, plus read-only
  `diet-plan-meals/`/`diet-plan-items/` sub-resources). See
  `docs/API.md` "Endpoints".

## [1.5.0] — 2026-08-27

### Added
- Open Graph/Twitter Card meta tags and a meta description on every
  page, for a nicer preview when a link to this instance is shared.
- A "Site & SEO" admin setting (Profile → Administration), off by
  default: whether search engines are allowed to index this instance
  at all — a self-hosted instance holding personal health data
  shouldn't be publicly indexed unless an operator deliberately opts
  in. Backed by both `/robots.txt` and a `<meta name="robots">` tag on
  every page.
- The front-page month calendar can now browse into future months
  too, not just past ones.
- Profile → "Delete account" — self-service, GDPR Article 17 account
  deletion. Everything exclusively yours (workout history, personal
  records, measurements, nutrition data, activities, feedback, API
  keys) is permanently deleted; a custom exercise/food/recipe/program/
  measurement/activity type you created is kept (not deleted) if
  anyone else on this instance is already using it, just no longer
  credited to your account. See `docs/SECURITY.md` "Account deletion
  (GDPR)" for exactly what this can and can't reach (backup archives
  made before deletion are the one honest exception).
- Profile → "Download your data" — GDPR Article 20 data portability.
  Browse everything you've logged as a plain page, or download it as
  a single JSON file, a `.zip` of spreadsheet-ready CSV files, or
  that same page as one HTML file to keep or hand to someone else.
- A privacy notice, shown on the login/signup pages and reachable
  again anytime from Profile — what's collected, why, your rights
  (including the two features above), and what's retained.
- The first-login onboarding modal now also asks for your timezone
  (pre-filled, so "Save" never actually requires picking one) —
  previously only available afterwards, from Profile, meaning every
  "today"/date-range boundary in the app used UTC by default until a
  new user happened to find that setting.

### Fixed
- Changing month on the front-page calendar reloaded and repainted
  the entire dashboard for a one-card change, visible as a brief
  flash every time — it now swaps just that card.
- Generating (or regenerating) two-factor backup codes could take
  several real seconds with no visible feedback, which could look
  like nothing had happened and invited clicking again mid-wait —
  racing a second request that could bounce you away before you ever
  saw the codes the first one had already generated. Now shows a
  loading page immediately and loads the result once it's ready.
- Profile → "Download your data" left out your last name and several
  display/privacy settings (height, and whether you show BMI,
  achievements, your name, or a Gravatar picture to others) — it only
  ever covered a handful of account fields, not everything on your
  account that isn't a credential.

## [1.4.1] — 2026-08-27

### Fixed
- A routine update (README.md "Updating": `docker compose pull && up
  -d`) could leave the site unreachable — `502`/"Connection refused"
  from nginx — until nginx itself was also manually restarted. nginx
  resolved the `web` container's hostname to an IP once at its own
  startup and cached it forever; `web` recreating with a new image got
  a new internal IP that nginx never noticed. nginx now re-resolves it
  every 10 seconds via Docker's own embedded DNS, so this self-heals
  after a routine update instead of needing a manual nginx restart.
- A CDN in front of a real deployment (Cloudflare) could keep serving
  a stale, hours-old CSS/JS file well after an update changed it,
  since the plain filename never changed and nginx sent no cache
  header telling it otherwise — a page could render badly broken
  (e.g. the front-page calendar's grid layout collapsing entirely)
  until the CDN's cache was manually purged. Static files now include
  a content hash in their URL (Django's `ManifestStaticFilesStorage`),
  so a real change always gets a new URL instead of colliding with a
  cached response for the old one — no CDN configuration or manual
  purge needed after an update. See `docs/ARCHITECTURE.md` "Static
  files".

## [1.4.0] — 2026-08-27

### Added
- Nutrition & calorie tracking (`apps/nutrition`): a step-by-step
  onboarding wizard estimating your calorie/macro needs (Mifflin-St
  Jeor, a suggested-not-self-reported activity level), goal-based
  targets with built-in safety limits, a food diary backed by an
  on-demand OpenFoodFacts lookup, recipes, a diet-plan builder, a
  dashboard with a weight-trend chart, and a dynamic calorie-
  adjustment suggestion once enough weight history exists. See
  `docs/NUTRITION.md`.
- Seven standalone nutrition calculators (`/nutrition/calculators/`):
  BMR/TDEE, macro split, body fat percentage (U.S. Navy method), daily
  water intake, BMI, waist-to-hip ratio, and time-to-goal-weight —
  quick estimates that don't require completing nutrition onboarding
  or setting a goal.
- A persistent sub-nav across every nutrition page (dashboard, diary,
  foods, recipes, diet plans, calculators, statistics), reported as
  needed once nutrition grew to more top-level sections than any other
  part of the app — reaching a sibling section no longer means
  scrolling back to the dashboard first. The dashboard's old "Quick
  links" card is gone; the sub-nav reaches the same places from
  everywhere, not just there.
- Adding a recipe ingredient is now search-and-pick (local foods, live
  OpenFoodFacts results, and barcode-number search) instead of a
  dropdown of foods you already had to create by hand — macros are
  pulled in automatically.
- Diet-plan meals can now hold more than one item — add extras
  alongside the auto-generated one, and remove any item individually.
- Searching for a food by its barcode number now works everywhere the
  food-search box appears (diary, recipe ingredients, diet-plan
  meals).
- A dedicated "Import from OpenFoodFacts" page (Foods → "Import from
  OpenFoodFacts") — search or browse by category to add a food to
  the shared library on its own, not just while logging something.
- Foods now show a Nutri-Score (A-E) and NOVA (1-4) badge when
  OpenFoodFacts has graded them — a real, independently-published
  healthiness/processing-level scale, not one this app invents.
- Every food-search box (diary, recipe ingredients, diet-plan meals,
  browse/import) can now scan a barcode with your camera, on browsers
  that support it, instead of only typing the digits in by hand.
- The nutrition dashboard has a single "Quick links" section reaching
  every part of nutrition (including Foods, which previously had no
  direct link) and a "+ Log food now" shortcut.
- The food diary's date header now has a date picker to jump straight
  to any day, not just step one day at a time.
- A recipe can now be logged to the diary for a past or future date,
  not only today.
- Admins can now merge duplicate foods in the shared library (Foods →
  select two or more → "Merge selected foods into one…") — every
  diary entry, recipe ingredient, and diet-plan item that referenced
  a duplicate is repointed at the kept food, never deleted.
- Admins can now force-refresh selected foods from OpenFoodFacts
  immediately (Foods → select → "Refresh selected foods from
  OpenFoodFacts") instead of waiting for the normal 14-day
  staleness-triggered refresh.
- A nutrition statistics page (Nutrition → "Statistics") — a 30-day
  calorie chart plus average daily calories/macros, alongside the
  current target for comparison.
- Every place you can add a food (the food diary, a recipe's
  ingredients, a diet-plan meal's items) now shows a "Most used"
  quick-add list of your top 10 most-used foods — one tap instead of
  a fresh search every time.
- A day in the food diary can now be copied to another date ("Copy
  this day to another date") — repeats every item logged that day
  onto a new date in one action.
- A recipe ingredient's quantity can now be edited directly, instead
  of having to delete it and re-add it through search.
- The recipe list now shows each recipe's calories per serving and
  can be searched by name.
- A recipe's nutrition breakdown now shows fiber, sugar, saturated
  fat, and sodium too, whenever at least one ingredient has that data.
- 18 built-in template recipes (six each for bulking, fat loss, and a
  balanced maintenance goal — e.g. "Bulk breakfast — Oats & peanut
  butter", "Fatburner lunch — Chicken & broccoli"), seeded into every
  installation including a brand new one, built from real OpenFoodFacts
  data and tagged with the meal (breakfast/lunch/dinner) each is meant
  for. They're shared with every user (not owned by anyone in
  particular), show up alongside your own recipes everywhere a recipe
  can be picked, and are what the diet-plan builder now actually
  suggests from — a lunch recipe is never suggested for breakfast.
- A diet plan can now span a full week instead of always repeating one
  day — the diet-plan builder varies which recipe/food fills each meal
  across the seven days (never the day's own calorie/macro target)
  so a week's meals aren't identical every day.
- A diet plan can now be marked active/inactive (Diet plans →
  Activate/Deactivate) — only one plan is ever active at a time — and
  the nutrition dashboard shows today's planned meals and macros from
  whichever plan is currently active. A weekly plan's page shows each
  weekday collapsed by default; tap one to see its meals.
- A month calendar on the front page (browsable to earlier months) —
  a barbell icon for a training day (colored for a personal record or
  an abandoned session), a moon for a rest day, and an arrow for
  whether your trailing 7-day average calories ran over or under
  target, colored by how far off. Icon-only by design; a "?" button
  explains what they mean, and tapping a day shows a short plain-text
  summary of it.
- A "Change goal" button on the nutrition dashboard's "Current goal"
  card, so a goal set during onboarding isn't stuck there forever —
  picking a new goal recalculates and saves a fresh calorie/macro
  target right away, without touching your target/goal history.

### Fixed
- The "Rest day"/"Training day" tag on the nutrition dashboard was
  hard to read and gave no explanation of what it meant; it's now a
  clearer outlined tag with a tooltip.
- A calorie target's saved "reason" text (e.g. "Estimated maintenance
  (TDEE) is..., adjusted for your goal...") was frozen in whatever
  language was active the moment it was calculated, so it stayed in
  that language forever afterwards regardless of the site's current
  language. It's now rebuilt from the underlying numbers every time
  it's displayed, in the language you're actually viewing it in
  (older targets saved before this fix keep their original frozen
  text, since the numbers behind it were never stored).
- The diet-plan builder, and swapping an item on an existing diet
  plan, couldn't see or use the template recipes above — only recipes
  you'd created yourself.
- The Foods/Recipes/Diet plans pages' "back" link went to the food
  diary instead of the nutrition dashboard, even when you'd arrived
  from the dashboard's own "Quick links" — pressing "back" landed
  somewhere you'd never actually been. The food diary itself had no
  way back to the dashboard at all. Both fixed.
- Date pickers on the recipe/diet-plan "log for date" fields and the
  onboarding wizard's date of birth showed blank instead of
  pre-filled whenever the site language wasn't English (e.g. in
  Finnish) — a browser rejects a date value that isn't in ISO
  format, and these were rendering it in the site's own date format
  instead.
- The quantity field on every food-search "Add" card (diary, recipe
  ingredients, diet-plan meals, browse) showed blank instead of
  pre-filled whenever the site language uses a comma as its decimal
  separator (e.g. Finnish) — same root cause as the date-picker fix
  above, for `type="number"` instead of `type="date"`.
- An OpenFoodFacts import-failure message in the food diary that was
  never translated.
- Logging a diet plan to the diary with an invalid date silently did
  nothing instead of showing the error.
- Some button rows in the nutrition UI could run past the right edge
  of the screen on mobile, especially with longer translated text.
- The Nutrition icon in the bottom navigation looked partially cut
  off compared to the other icons.
- Several actions across the training log, body measurements,
  activities, exercises, and programs (starting/completing/deleting a
  workout session, logging a set, editing/deleting a measurement or
  activity entry, deactivating a custom exercise, copying a program,
  and more) crashed instead of redirecting to login when accessed
  while signed out.
- A weekly-volume-chart test that intermittently failed whenever the
  calendar date happened to fall on a Monday.
- The recipe list's calories-per-serving figure ran one extra database
  query per recipe shown; now a fixed number of queries regardless of
  how many recipes are listed.
- The admin food-merge page's radio buttons, and the API-key-created
  page's key-secret field, had no accessible name for screen readers.
- The nutrition stats page's daily-calorie chart could show several
  decimal places (e.g. "361,5500 kcal") instead of a whole number.

### Added — API
- A public API context for nutrition (`foods/`, `meal-slots/`,
  `recipes/`, `recipe-ingredients/`, `diary-entries/`,
  `nutrition-goals/`, `nutrition-targets/`) — every other part of the
  app already had one, nutrition was the one exception. Goals/targets
  are read-only, matching how personal records already work in the
  API: both are historized and only ever change through their own
  dedicated service functions, never a raw write. See `docs/API.md`.
- The API keys page has an in-app "?" documentation button — base URL,
  auth header, permissions, units, and copy-pasteable curl/Python
  examples using this deployment's own real address.

### Development
- Test coverage measurement (`coverage`) is now part of the dev
  toolchain — `coverage run -m pytest && coverage report`.

## [1.3.0] — 2026-08-26

### Added
- Authentik single sign-on (OIDC) as an optional login method
  alongside username/password + TOTP, `docs/SECURITY.md` "Single
  sign-on (Authentik / OIDC)" — matches an Authentik login to an
  existing local account by email or auto-provisions a new one,
  entirely opt-in (nothing activates unless `AUTHENTIK_URL`/
  `AUTHENTIK_CLIENT_ID`/`AUTHENTIK_CLIENT_SECRET` are all set), with
  an optional `AUTHENTIK_REQUIRED_GROUP` restriction and a
  `DJANGO_PASSWORD_LOGIN_ENABLED` switch to close local password
  login once every user has an Authentik-linked account.
- An opt-in Gravatar profile picture, Profile → "Show my Gravatar
  picture" (off by default) — see `docs/SECURITY.md` "Gravatar
  profile picture" for why: it's the only place this app talks to a
  server outside its own infrastructure.

### Fixed
- Local-password signup and a 2FA-verified login crashed with a
  `ValueError` once Authentik SSO was enabled (two authentication
  backends configured at once).
- Bar and line charts (Analytics, Progress) rendered invisible for
  any user with a comma-decimal UI language (Finnish, Swedish,
  Russian) — Django's locale-aware number formatting broke the
  underlying SVG coordinate syntax.

## [1.2.0] — 2026-08-15

### Added
- Optional two-factor authentication (TOTP), Profile → "Two-factor
  authentication" — QR-code setup, single-use backup codes, and an
  admin recovery action for a fully locked-out user. See
  `docs/SECURITY.md` "Two-factor authentication".
- The login and signup pages now show the IronStack logo/wordmark.
- A site-wide disclaimer footer on the login and signup pages,
  editable from Django admin (default text provided, blank hides it).
- A one-time, skippable onboarding prompt shown right after a new
  user's first login, asking for name/email/starting weight/height/
  units and explaining what each is used for.

## [1.1.0] — 2026-08-15

### Added
- Backup & restore, two independent mechanisms (`docs/BACKUP.md`):
  `scripts/backup.sh`/`restore.sh` on the Docker host, and an
  admin-only web UI (Profile → Administration → Backups) to create,
  download, and restore backups without leaving the app.
- A privacy toggle, "Show my name to others"
  (`User.show_name_to_others`) — lets a user's first name appear next
  to their username in the achievements carousel and "Recently
  active" list, separate from whether their data appears there at
  all (`show_achievements`).
- The dashboard greeting now addresses a user by first name when
  they've set one.
- A red-bordered "danger zone" around the profile page's staff-only
  Admin/Backups cards.
- This changelog viewer — click the version number on the profile
  page.

### Changed
- BMI moved from the dashboard/profile page to the "Body weight"
  measurement history page, next to where a body weight actually gets
  logged.
- The achievements API's `AchievementSerializer` field `username` is
  renamed `display_name`, to match the privacy toggle above (a small
  breaking change to that response shape).

### Fixed
- The bottom nav's "Home" link lit up alongside "Progress" while
  viewing the Progress page (both pages' bare `url_name` happened to
  be `"dashboard"` within their own app).
- A web-UI backup restore that failed partway through (e.g. a
  `pg_dump`/`pg_restore` client/server version mismatch) used to leave
  the live database completely empty; restore now loads into a
  freshly created database first and only swaps it in once that
  succeeds.

## [1.0.0] — 2026-08-14

Initial versioned release, marking the point `VERSION` started being
tracked — not a rewrite or a fresh start. Everything below already
existed going into this release; see `README.md` for the complete,
detailed history of how each part was built.

### Added
- Workout logging with an explainable smart-weight-suggestion and
  progression engine, automatic personal-record detection, and
  reusable program templates (`apps/workouts`, `apps/progression`,
  `apps/records`, `apps/programs`).
- Body measurement and non-gym activity tracking (`apps/measurements`,
  `apps/activities`), with charts and training-volume/PR analytics
  (`apps/analytics`).
- A public API (Django REST Framework) with per-context CRUD API keys,
  admin-adjustable rate-limit tiers, and self-service key management
  (`apps/api`).
- Six-language UI (English, Finnish, Swedish, Russian, Italian,
  Estonian), an installable PWA, and a Django admin re-themed to match
  the rest of the app.
- Application version metadata (this file's own reason for existing) —
  a `VERSION` file, optional git-commit/build-date OCI image labels
  (`scripts/build.sh`), and a `version_info` management command, laid
  out as the intended single source of truth for a future
  backup/restore feature to stamp and check archives against.
