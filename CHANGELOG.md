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

## [Unreleased]

### Added
- Stretching: a new section with 32 built-in stretches (with
  instructions and target muscles), four ready-made routines, your own
  stretches and routines, and a guided session player — get-ready, hold
  and rest countdowns per side and set, chimes and vibration, a screen
  wake lock, pause/±10 s/skip, and progress that survives a reload.
  Sessions can also be logged afterwards with just a duration; the
  overview shows your streak, recent sessions and minutes per week.
- After a finished workout, a card suggests a cool-down routine matched
  to the muscles you trained (never started automatically).
- Stretching days show as a dot on the dashboard calendar.
- "Track stretching" preference (profile and onboarding) to hide the
  stretching tab and suggestions.
- REST API: `stretches/`, `stretch-routines/`, `stretch-routine-items/`
  and `stretch-sessions/` under a new "Stretching" key permission area.
- Stretching data is included in "Download your data" and removed on
  account deletion.

### Changed
- The workout rest timer's sound handling moved into a shared
  `timer-audio.js` used by both timers (no behaviour change).

## [1.21.0] — 2026-09-21

### Changed
- Open Food Facts integration now follows OFF's API guidelines: text
  search uses Search-a-licious instead of the deprecated `/cgi/search.pl`;
  online search runs when you press "Search Open Food Facts" instead of on
  every keystroke (barcodes still look up automatically); requests stay
  under OFF's 10 searches / 15 reads per minute limits with a shared
  budget, a failure cooldown and 24-hour search caching; the category
  browse list is a static curated list.
- Requests identify the instance operator in the User-Agent
  (`OFF_CONTACT_EMAIL` or the new admin "contact email" setting).
- The admin "refresh selected foods" actions now handle at most 12 foods
  per run (Open Food Facts' read limit) and say how many were left.

### Added
- Open Food Facts / ODbL / CC BY-SA attribution on online search results,
  browse, category and food detail pages.
- `OFF_API_BASE` / `OFF_SEARCH_BASE` settings to point at Open Food
  Facts' staging server (its public basic-auth login is sent for
  `*.openfoodfacts.net`).
- Translations for the new strings in all six languages.

## [1.20.0] — 2026-09-21

### Added
- Breadcrumbs on detail pages (programs, exercises, recipes, diet plans,
  foods, workouts, body tracking, activities) so it's clear where you are;
  a loading bar while live searches run; calorie *and* macro progress bars
  on the nutrition overview; first/last dates under line charts; a
  "Log body weight" shortcut on the dashboard; set count and volume on a
  workout's page; confidence level and per-set edit links in training mode.
- The web-app manifest now follows your theme (splash screen and title bar)
  and ships maskable icons, so the installed icon isn't cropped on Android.

### Changed
- Profile preferences are collapsed by default (they open on their own if
  a save fails), so Account, Social and Coaching are no longer buried
  under twenty fields. Backups shows the backup list first, with a
  "Creating backup…" state, and Delete is styled as destructive.
- One consistent vocabulary: no "+" on buttons, "Log to diary" everywhere,
  "Add workout", "Back to dashboard"; the food page separates "Find a new
  food" from filtering your own list. "Start freeform workout" now sits at
  the end of the Workouts page instead of under its heading.
- Every form now renders through the shared field template (labels,
  checkboxes, help text and errors linked for screen readers); card headings
  no longer skip heading levels; touch screens get full-size row buttons.
- Themes are one block each (`light-dark()`) instead of three copies — no
  colour changes; design tokens replace hard-coded z-indexes, shadows and
  font sizes; the last repeated inline styles became utility classes;
  Complete workout asks first when exercises still have sets left.

## [1.19.0] — 2026-09-20

### Added
- Personal trainer coaching: turn on "I'm a personal trainer / coach"
  in Profile → Preferences to let other users request coaching from
  you (never the other way around) — toggle "Accepting new coaching
  requests" off anytime to stop new requests without affecting
  clients you already have. A trainer can share any gym program or
  diet plan with specific clients they choose, letting each one
  import their own independent copy with one click — no file to
  download and re-upload. When a trainer later updates a shared plan,
  an imported copy shows "Coach update available", with a
  plain-language preview of exactly what changed before choosing to
  apply it or keep your own version. Accepting a coaching request is
  the only consent needed — from then on your coach can see your
  training history, PRs, and body-weight data on their own "My
  clients" page, until either of you ends the relationship. Programs
  and diet plans imported from a coach now show in their own "From my
  coach" group on your programs/diet plans page, separate from your
  own.

### Changed
- UI/UX audit pass. Logging a set now shows what was wrong with the
  weight or reps you entered (it used to re-render silently), numeric
  fields open a numeric keypad, and a failed request (server error,
  dropped connection) shows an error message instead of doing nothing.
- Destructive actions now share one accessible confirmation dialog
  (keyboard-trapped, Escape-cancellable) instead of the browser's
  built-in one; deleting a single set and removing an exercise image
  now ask first. Fixes the "Delete this group?" confirm, whose
  apostrophe could break the prompt.
- Saving, deleting and logging on programs, exercises, measurements
  and activities now shows a confirmation message; errors stay on
  screen until dismissed and other messages pause while hovered.
- Workout history is paginated and each entry shows exercise count,
  sets, volume and duration; measurement and activity history tables
  are paginated too. The dashboard offers a "Start freeform workout"
  button when nothing is in progress, and Progress gains a per-exercise
  strength-trend picker.
- Form fields link their errors to the input for screen readers, with
  a summary at the top of a form; "Back"/"Cancel" links are styled as
  links so they no longer look like primary actions. Modals now move
  focus in, trap Tab and restore focus on close.
- Buttons gain hover/active/disabled states, a wider desktop layout,
  design tokens and utility classes (replacing most inline styles),
  and tables get a scroll hint on phones. The manifest no longer
  locks the installed app to portrait.

## [1.18.0] — 2026-09-20

### Added
- Export any of your own gym programs, nutrition recipes, or diet
  plans as a `.json` file, and import one back in — from your own
  account, from someone else's, or from a completely different
  self-hosted IronStack instance. Custom exercises, custom meal slots,
  and foods originally imported from OpenFoodFacts are all
  reconstructed automatically (an OFF-sourced food is looked up again
  live by its own barcode) and matched against your own existing ones
  first rather than duplicated; an import that fails partway never
  leaves anything behind. A diet plan always imports inactive, so it
  never silently replaces whatever plan you're currently following.
  The file also carries the exporting instance's app version, shown
  as an informational note if it differs from yours.
- Every name in the dashboard's achievements carousel and "Recently
  active" list now links to that person's own profile page — recent
  PRs, streak, workout count, total weight lifted, and how long
  they've been a member. Nothing about food, calories, or body
  weight. Turn off "Share my activity" in Profile → Preferences →
  Privacy to keep others from viewing yours (you can still always
  view it yourself).

### Fixed
- The iOS home-screen app icon's dumbbell now shows a connected
  handle bar — it rendered as nearly invisible on the actual shipped
  icon sizes, even though it looked fine in a regular browser.

## [1.17.0] — 2026-09-19

### Added
- Every meal card in the food diary (except "Other") now has a "Save
  as recipe" button, turning everything logged there into a new,
  reusable recipe at the exact amounts eaten.
- Nutrition tracking can now be turned off — asked about during your
  first login, and switchable anytime from Profile → Preferences →
  Features. Off hides nutrition from navigation and the dashboard;
  your existing nutrition data is never deleted, and its pages stay
  reachable directly by a link either way.

### Fixed
- The dashboard calendar's calorie trend arrow no longer shows up on
  future days — its trailing 7-day average could still pull in
  already-logged past days even though the future day itself has
  nothing logged.
- Clicking Previous/Next on the "All foods" list now keeps your place
  at the pagination buttons instead of jumping back to the top of the
  page.
- The API keys page's "Using the API" documentation now lists the
  nutrition profile and diet plan endpoints — live for a while, but
  missing from this in-app reference.
- Adding, editing, removing, or logging a food/recipe to a meal in the
  food diary now returns you to that meal's own card instead of
  jumping to the top of the page.

### Performance
- Food list thumbnails now load OpenFoodFacts' own smaller,
  pre-generated photo instead of the larger one used on a food's own
  page, cutting how much needs to download while browsing the list.

## [1.16.0] — 2026-09-19

### Added
- Every food now has its own nutrition-facts page — tap its name
  anywhere it appears (the diary, a recipe's ingredients, "most
  used", search results, a diet plan's meal preview) to open it.
- Foods imported from OpenFoodFacts now show a photo (in the food
  list and on the food's own page) and their OFF categories — makes a
  food much easier to recognize at a glance.
- The "All foods" list is now paginated (20 per page) and can be
  filtered by name/category and sorted by name, date added, category,
  or calories (ascending or descending) — all combinable at once.
- A food's own page now shows its product information from
  OpenFoodFacts (package size, categories, labels, allergens,
  ingredients) when available.
- Foods imported from OpenFoodFacts now show a typical price, sourced
  from Open Prices — on the food's own page, next to a recipe
  ingredient's calories, and next to a diary entry's calories.
- A food imported from OpenFoodFacts now shows a prompt to edit it on
  OpenFoodFacts or add a price on Open Prices, linked straight to
  that product.
- Diet plans can now be started from scratch (every meal left empty
  for you to fill in) instead of always auto-generating suggestions.
- The diet plan page now has a small, collapsible "Totals" box
  showing how much of the day's calorie/macro target is covered so
  far and how much is left.
- If a food imported from OpenFoodFacts has no photo yet, its own
  page now links to that product's page on OpenFoodFacts instead of
  showing nothing.

### Changed
- On the "Add food" page, the recipe ingredient page, and the diet
  plan item page, search/barcode scan now comes above "Most used"
  instead of below it.
- Double-tapping a food or recipe name no longer zooms the page in on
  iOS Safari.
- The Recipes page now lists your own recipes (most recent first,
  paginated 5 per page) separately from the built-in template
  recipes (in their own box, paginated too).
- Clicking Previous/Next on a paginated list (currently the Recipes
  page) now keeps your place at the pagination buttons instead of
  jumping back to the top of the page.

### Fixed
- Tapping "+ Add food" under a specific meal (e.g. Dinner) no longer
  lands on the add-food page with a different meal (Breakfast)
  preselected — food logged there went to the wrong meal until you
  noticed and switched it by hand.

## [1.15.0] — 2026-09-16

### Added
- The food diary now offers a morning snack (between breakfast and
  lunch) and an afternoon snack (between lunch and dinner), plus an
  "Other" category for food that doesn't fit any named meal.
- Profile → Preferences has two new settings: Theme (Default, Nordic,
  Vaporwave, Earth, Zen) and Appearance (Dark, Light, or Auto to match
  your device). Every theme has its own dark and light look.

### Changed
- "Recent PRs" (dashboard and Analytics) now shows one card per
  exercise instead of one card per record type, with only the
  headline figures (max weight, estimated 1RM, best set/session
  volume) visible by default — tap an exercise to reveal any
  rep-specific/rep PRs plus each record's source set (weight × reps)
  and date.

### Fixed
- The food diary's "Previous day"/date/"Next day" row no longer wraps
  onto three separate lines on a phone-width screen.

## [1.14.1] — 2026-09-14

### Fixed
- The live-training "quick set" panel (weight/reps/RPE/notes) still
  made an iPhone zoom the whole page in on focus — its compact labels'
  smaller font-size was inherited by the input/textarea nested inside
  them, well under the 16px iOS Safari needs to not auto-zoom. Audited
  every form in the app for the same class of bug and found two more:
  the group invite-link field and a new API key's textarea both use a
  monospace font, which browsers apply a separate, smaller default
  size to unless the page sets one explicitly — even when a normal
  16px is already being inherited from further up the page.
- The dashboard's "Recently active" list showed how long ago a
  session *started*, not how long ago this person actually stopped
  training — a long session that just finished read as "hours ago"
  instead of "just now".
- Live camera barcode scanning (the "Scan barcode" button next to food
  search boxes) never actually detected anything on any browser using
  the vendored ZXing fallback — the camera opened and the video played
  fine, but the scan callback was hooked up to a one-shot decode
  method that ignores a callback argument entirely, so it silently
  never ran. Chrome/Android users with the native BarcodeDetector
  weren't affected.

### Performance
- Personal-record detection (checked on every set you log) no longer
  re-fetches an exercise's entire logged history into Python just to
  find its highest set/session volume — the database now computes
  that directly. Only noticeable on an exercise with a long logging
  history, where it kept every new set's response time growing with
  that history's size.

## [1.14.0] — 2026-09-13

### Added
- Exercises can now hold a small gallery of instructional images, each
  with an optional caption, plus a dedicated written instructions
  field — shown together in a new "Instructions" section on the
  exercise detail page. A user manages their own custom exercise's
  images from that page; system exercises' images are admin-only. How
  many images a single exercise may hold is capped by a new
  admin-adjustable setting (Django admin → Exercise image settings).
- 27 of the 28 seeded system exercises now ship with an instructional
  image and written step-by-step instructions out of the box, sourced
  from wger.de's own open, CC-BY-SA-licensed exercise database (a
  handful rewritten from scratch where the source text was too thin or
  named the wrong equipment for how this project seeds that exercise).
- The public API's exercise endpoint now includes `instructions` and
  the new instruction images (read-only), matching what the exercise
  detail page itself shows.

### Fixed
- All six locales (`en`/`fi`/`sv`/`ru`/`it`/`et`) are fully translated
  again — `makemessages` had drifted out of sync with a pre-existing
  backlog from `apps.social`'s friend/group notification muting, Web
  Push permission prompts, and backup encryption warnings, silently
  falling back to English with nothing to flag it.
- The "new/edit exercise" form's muscle group checkboxes and equipment
  dropdown rendered in English regardless of a user's own language
  setting — they were never given the `label_from_instance` override
  every other exercise picker in this app already uses.

## [1.13.1] — 2026-09-10

### Fixed
- White text on the accent color (every primary button, tags, the
  skip-to-content link, the active range-filter tab) fell short of
  WCAG AA's minimum contrast — found by a new automated accessibility
  check, not visually obvious on its own.

## [1.13.0] — 2026-09-10

### Added
- A dashboard card reminding you to log a body measurement once you
  haven't logged one (of any type) in 14 days — on by default, with a
  new Profile → Preferences → Notifications toggle to turn it off.
- A "Statistics" card on each body measurement's history page: current
  value, change since your first log, lowest/highest/average, entry
  count, and how long you've been tracking it.
- A program's prescribed exercises now link to their exercise detail
  page, to check the description, equipment, or muscle groups without
  leaving to search the exercise library.

## [1.12.0] — 2026-09-09

### Added
- A server-side push notification when the rest timer finishes, for
  when the client-side one can't fire (the phone backgrounded or
  locked) — needs Web Push already turned on (Profile → Notifications).
- Interactive API docs at `/api/docs/` — a Swagger UI generated
  straight from the real API, with a working "Try it out" against your
  own API key.
- Optional backup encryption (`BACKUP_ENCRYPTION_KEY`) — every backup,
  either mechanism, can now be encrypted at rest.
- Optional TOTP secret encryption at rest (`TOTP_ENCRYPTION_KEY`).
- Screenshots on the README.

### Fixed
- An API key with permission to create programs could attach another
  user's private custom exercise to their own prescription or
  performed exercise by id — the exercise ownership check every other
  write in the API already had.

## [1.11.1] — 2026-09-09

### Fixed
- The rest timer's "time's up" notification showed a literal
  "undefined" instead of its actual title/body text.

## [1.11.0] — 2026-09-08

### Added
- A GitHub link to the profile page footer, next to the privacy
  notice and version number.

### Fixed
- Tapping the group invite-link field, or double-tapping anywhere,
  zoomed the page in on iOS Safari.
- The mobile bottom-nav's icons sat cramped and low, crowded against
  the home-indicator inset on phones that have one.
- The rest timer lost track of time while the phone's screen was
  locked, and its "time's up" notification never actually displayed
  on iOS.
- The two-factor verification code field offered no autofill hint to
  password managers.
- The Content-Security-Policy header blocked a password manager
  extension's own inline autofill-suggestion overlay from loading.
- Every page and static asset was served uncompressed by nginx.

## [1.10.1] — 2026-09-03

### Changed
- A group page's routine actions (Mute notifications, back to Groups)
  are grouped together; Delete/Leave group is now the very last thing
  on the page instead of sitting directly between two ordinary
  buttons with nothing setting it apart but its own red color.

### Fixed
- A group's invite link could run off the right edge of the screen —
  it's a read-only text field with a Copy button now, instead of a
  long unbroken line of plain text.
- The "Cancel" link on the edit-group page showed as "Back", which a
  non-English locale could only translate as the exercise muscle
  group of the same name (e.g. Finnish "Selkä", the body part) since
  both shared the same source string.

## [1.10.0] — 2026-09-03

### Added
- An operator contact address (`DJANGO_ADMIN_CONTACT_EMAIL`), shown in
  the privacy notice for anyone to reach whoever runs this instance.
- Rest timer: a system notification when it finishes and the page
  isn't visible (phone locked, a different app/tab in front) — on top
  of the beep and the visible countdown reaching 0:00, and only ever
  for someone who's already turned on notifications.
- Calendar: hovering a day (desktop) shows its details immediately, no
  tap needed; tapping outside the calendar closes an open day; a new
  personal record's own day is gold instead of sharing a color with
  an unrelated nutrition icon a few cells over.
- The bottom-nav Profile icon shows your own Gravatar picture, when
  you've turned that on — previously only shown on the profile page.

### Changed
- Every page's title bar now holds only the title — action buttons,
  status tags, and the like moved to their own row right below it,
  for a consistent look across the whole app.
- Profile page reorganized into named sections (Account, Social, Your
  data, Support) instead of one long list, with real toggle switches
  in place of plain checkboxes.
- Dashboard: the month calendar moved below what you're actually
  likely checking (continue a workout, this week's numbers, recent
  PRs) instead of opening the page and pushing them off-screen — still
  reachable without a click, just lower.
- Toast notifications (e.g. "Preferences saved.") appear in the
  bottom-right corner on desktop instead of spanning the top of the
  window.

### Fixed
- Searching exercises (or anything relying on the same seeded-name
  translation) in a non-English language now actually finds matches —
  it used to only ever match the stored English name.
- The 32×32 favicon rendered as an illegible cropped fragment in a
  real browser tab.
- Barcode scanning could silently do nothing forever on a device where
  the camera opened but the browser's native barcode decoder wasn't
  actually usable — now falls back to the bundled decoder instead.
- A month change in the calendar could leave a day's popover showing
  stale data from the previous month, or nothing at all.
- Meal names (Breakfast, Lunch, ...) were never translated.
- The nutrition section's own tab bar could leave you on a tab
  scrolled out of view, with nothing showing which section you were
  actually in.
- A stale cached copy of the app's own JavaScript/CSS could keep
  serving from before an update, sometimes well after installing a
  new version — the cache now invalidates itself automatically instead
  of relying on a hand-updated version number.

## [1.9.1] — 2026-09-02

### Changed
- Deploy-time setup (database migrations, static files, translations,
  and 1.9.0's new-version notification) now lives inside the Docker
  image itself (`docker-entrypoint.sh`) instead of `docker-compose.yml`.
  No effect on how updating works (`docker compose pull && up -d`
  still does everything) — it just means a self-hoster's own copy of
  the compose file can no longer fall out of sync with what actually
  runs on startup.

## [1.9.0] — 2026-09-02

### Added
- A push notification when a new version is deployed — "IronStack
  X.Y.Z is now running — tap to see what's new" — for anyone
  subscribed to notifications (Profile → Notifications). Tapping it
  opens the same changelog you'd already get from tapping the version
  number on your profile page.

## [1.8.1] — 2026-09-02

### Fixed
- Profile → "Download your data" (the plain, non-download page) threw
  a 500 for any user with so much as one friend, blocked user, group
  membership, direct/group message, or registered push subscription —
  a handful of sections the export builds by hand rather than through
  Django's generic serializer, which the page's own row-normalizing
  code didn't know about.

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
