# Roadmap

## v1

Foundation:
- Django
- PostgreSQL
- Docker
- authentication

Training:
- exercise library
- custom exercises
- programs
- workout templates
- workout scheduling
- workout logging
- historical sessions

Intelligence:
- PR engine
- progression engine
- smart weight suggestions

Tracking:
- body measurements
- manual activities

Analytics:
- strength trends
- training volume
- body trends
- activity trends
- PR dashboard

UI:
- mobile-first workout flow
- responsive desktop interface
- accessibility
- loading/error/empty states

## v2 — Nutrition

**Explicitly requested, in progress — see `docs/NUTRITION.md`.** Moved
out of "future possibilities" below (where it sat as "do not implement
merely because the architecture allows it") once it stopped being
hypothetical.

- calorie/macro needs estimate (BMR/TDEE) and goal-based targets
- fat-loss/maintenance/muscle-gain goals with a chosen rate, historized
- food diary, meals, recipes
- guided diet-plan builder
- weight-trend-based calorie adjustment suggestions
- a nutrition dashboard, integrated with existing body-weight tracking
  and (where the data supports it) training-day awareness

## Future possibilities

Architecture may later support:
- PWA/offline functionality
- Apple Health
- Google Fit
- smartwatch integrations
- native mobile clients
- an external food/barcode database integration
- advanced statistical models
- additional progression algorithms

Do not implement these merely because the architecture allows them.

**PWA — partially done, on explicit request.** The app is installable
(web manifest, icons, a minimal service worker) — see
`docs/UI.md` "Implementation" → PWA. Full **offline functionality**
(logging a workout with no connection, syncing later) is deliberately
still not built: this app's core principle is that workout history stays
historically trustworthy, and naive offline caching of pages/forms risks
showing stale data or silently losing a logged set that never reached
the server. The service worker installed only caches static assets
(CSS/JS/icons); every page, form, and HTMX response always goes to the
network. Building real offline data entry would need an explicit
queue-and-sync design (e.g. IndexedDB + background sync with clear
"pending" UI state) — a deliberate future decision, not a natural
extension of what's here now.
