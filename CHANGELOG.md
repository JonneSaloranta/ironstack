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
lands and remains the authoritative history.

## [Unreleased]

### Added
- Nutrition & calorie tracking (`apps/nutrition`): a step-by-step
  onboarding wizard estimating your calorie/macro needs (Mifflin-St
  Jeor, a suggested-not-self-reported activity level), goal-based
  targets with built-in safety limits, a food diary backed by an
  on-demand OpenFoodFacts lookup, recipes, a diet-plan builder, a
  dashboard with a weight-trend chart, and a dynamic calorie-
  adjustment suggestion once enough weight history exists. See
  `docs/NUTRITION.md`.
- Four standalone nutrition calculators (`/nutrition/calculators/`):
  BMR/TDEE, macro split, body fat percentage (U.S. Navy method), and
  daily water intake — quick estimates that don't require completing
  nutrition onboarding or setting a goal.
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

### Fixed
- Date pickers on the recipe/diet-plan "log for date" fields and the
  onboarding wizard's date of birth showed blank instead of
  pre-filled whenever the site language wasn't English (e.g. in
  Finnish) — a browser rejects a date value that isn't in ISO
  format, and these were rendering it in the site's own date format
  instead.
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
