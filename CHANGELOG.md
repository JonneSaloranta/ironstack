# Changelog

All notable changes to IronStack are recorded here, in the style of
[Keep a Changelog](https://keepachangelog.com/). The running version
lives in the repo-root `VERSION` file, not this document — see
`docs/ARCHITECTURE.md` "Versioning" for how that value gets baked into
a build and surfaced in the app (profile page footer, `version_info`
management command).

This file exists to answer "what changed between two versions", not to
duplicate the detailed, ongoing build log — that's `README.md`'s
"Status" section, which is updated with every feature as it lands and
remains the authoritative history.

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
  out as the intended single source of truth for a future backup/
  restore feature to stamp and check archives against.
