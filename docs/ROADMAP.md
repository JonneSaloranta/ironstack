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

**Explicitly requested, complete — see `docs/NUTRITION.md`.** Moved
out of "future possibilities" below (where it sat as "do not implement
merely because the architecture allows it") once it stopped being
hypothetical.

- calorie/macro needs estimate (BMR/TDEE) and goal-based targets
- fat-loss/maintenance/muscle-gain goals with a chosen rate, historized
- food diary, meals, recipes (automatic macros from ingredients)
- guided diet-plan builder
- weight-trend-based calorie adjustment suggestions
- a nutrition dashboard, integrated with existing body-weight tracking
  and (where the data supports it) training-day awareness
- a 30-day nutrition statistics page, a "most used foods" quick-add
  (ranked by usage across the diary/recipes/diet plans combined), and
  copying a day's diary to another date
- OpenFoodFacts integration: search, browse by category, or scan a
  barcode with your camera; Nutri-Score/NOVA badges where OFF grades a
  product
- full public API support (`ApiContext.NUTRITION`), matching every
  other domain — see `docs/API.md`

## v3 — Friends & groups

**Explicitly requested, complete — see `docs/SOCIAL.md`.** Went
through the same door nutrition tracking did for v2:
`docs/PRODUCT_REQUIREMENTS.md`'s "explicitly out of scope for v1"
listed *social network* and *messaging* — "do not implement unless
explicitly requested" — until they were.

- friend requests (send/accept/decline), an opt-out privacy setting
  controlling who can send one
- blocking another user
- groups with owner/admin/member roles; a short, unguessable invite
  link, or a direct invite to a friend (its own opt-out privacy
  setting)
- direct messages and group chat, HTMX-polled, no separate
  notification system
- a small badge on the Profile nav icon for anything waiting on you
- the two privacy settings asked once during onboarding, editable
  anytime from Profile, and exposed on the public API alongside the
  other display preferences

## Future possibilities

Architecture may later support:
- PWA/offline functionality
- Apple Health
- Google Fit
- smartwatch integrations
- native mobile clients
- advanced statistical models
- additional progression algorithms

Do not implement these merely because the architecture allows them.

**An external food/barcode database integration — done, see v2 above.**
Was listed here as a future possibility; OpenFoodFacts search/browse/
barcode-scan is now a real, shipped part of nutrition tracking
(`docs/NUTRITION.md` "OpenFoodFacts integration"), not hypothetical.

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
