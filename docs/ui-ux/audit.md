# UI/UX audit - 2026-10-28

Second whole-application audit. The first one (2026-10-01, a template
read-through) is summarised in `docs/UI_COMPONENTS.md` "Audit log"; this
one renders every page in a real browser.

Related: [design-system.md](design-system.md) (tokens and decisions),
[component-inventory.md](component-inventory.md),
[implementation-plan.md](implementation-plan.md). The rulebook every
template follows stays `docs/UI_COMPONENTS.md`.

## Method

| Check | How | Coverage |
|---|---|---|
| Routes | URL resolver dump (`get_resolver()`), 230+ patterns | every app |
| Rendering | `apps/core/test_ui_audit_crawl.py` (Playwright + live server, seeded data incl. long Finnish names), superuser + personal trainer | 114 GET pages rendered (127 URLs requested; 12 were POST-only endpoints -> 405, 1 is the 404 page) |
| Viewports | 320, 390, 768, 1280, 1680 px wide | every page, dark appearance |
| Overflow / layout | `apps/core/layout_check.js` (page overflow, off-screen, out-of-card, row actions under text / misaligned) | every page x 5 widths |
| Accessibility | vendored axe-core 4.10.2, tags `wcag2a/aa`, `wcag21a/aa`, `wcag22aa`, `best-practice` | every page at 390 px, dark **and** light appearance |
| Page facts | one `<h1>`, `<title>` format, inline styles, targets < 24 px, primary-button count | every page at 390 px |
| Visual review | full-page screenshots at 390 (dark + light) and 1280 (dark), 381 images | ~20 pages looked at by eye (dashboard, programs, workout detail, training mode, nutrition, recipes, measurements, profile, data export, API keys, assistant settings, groups, messages, analytics, login); the rest only through the automated checks above |
| Static | grep of templates/CSS against the `UI_COMPONENTS.md` rules | all 416 templates, `base.css` |

**Not verified** (needs a person or a device): screen-reader output
(VoiceOver/TalkBack), real iOS/Android touch behaviour, 200 %/400 % zoom
reflow beyond the 320 px check, the four non-default color themes (axe
ran on the default theme only; the token contrast for all themes was
computed separately for the changed tokens - see UI-002), signed-out
pages as an anonymous user (the crawl was signed in), and every
interaction state of every component (hover/active/loading were checked
in CSS, not driven per component).

## Baseline

- `docker compose exec -T web pytest`: 2225 passed, 3 skipped.
- Already in good shape (confirmed by the crawl, not assumed):
  - **no horizontal overflow, off-screen element or misaligned row on
    any page at any of the five widths**;
  - every rendered page has exactly one `<h1>` and a
    `<Page> - IronStack` title;
  - no `style=""` except data-driven progress-bar widths (allowed), no
    `confirm()`, no `form.as_p`, no inline event handlers;
  - no axe violations in the WCAG A/AA rule sets except those listed
    below.

## Findings

Severity: **Critical** blocks a task or fails WCAG A for a whole
control; **High** fails WCAG AA or breaks a core flow; **Medium**
inconsistency or friction users notice; **Low** polish / maintainability.

Status: **Fixed** (verified, method in the last column), **Open**,
**Accepted** (deliberate, listed in `UI_COMPONENTS.md` "Accepted
exceptions"), **Deferred** (needs a product decision).

| ID | Location | Category | Finding | Why it matters | Expected | Severity | A11y (WCAG) | Fix | Status | Verification |
|---|---|---|---|---|---|---|---|---|---|---|
| UI-001 | `/api/keys/new/`, `templates/api/key_form.html` | Forms / a11y | The 40 permission checkboxes (context x create/read/update/delete) have no label: axe `label` (critical) on every one. They are also bare 13x13 px. | A screen reader announces "checkbox, not checked" 40 times with no context; tiny targets. | Each checkbox named "<Action> - <Context>", target >= 24 px. | Critical | 1.3.1, 4.1.2, 2.5.8 | `aria-label` per checkbox from the row/column names; native checkbox size raised globally (UI-004). | Fixed | axe `label`: 80 nodes -> 0; test `test_every_permission_checkbox_is_named_for_screen_readers`; screenshot |
| UI-002 | `.tag-muted` (19 templates: API keys, abandoned sessions, inactive items, templates, ...) | Color | Light appearance: `--color-text-muted` on `--color-border` = 4.26:1 at 12 px. | Below AA for small text; the tag carries the status ("Revoked", "Abandoned"). | >= 4.5:1 in every theme and appearance. | High | 1.4.3 | Tag background becomes a lighter tint of the border color (`color-mix`) so the same muted text passes. | Fixed | axe `color-contrast` on `/api/keys/` -> 0 (light + dark); computed >= 5.05:1 for all 5 palettes x 2 appearances |
| UI-003 | `.card-untracked` (measurement and activity type lists) | Color | A type with nothing logged is dimmed with `opacity: 0.6`, which drops its tag to 2.56:1 (light) / 3.2:1 (dark) and its muted text to 2.59:1. | Text the user must read to choose a type fails AA. | Visually quieter, but every text >= 4.5:1. | High | 1.4.3 | Replace the opacity with a dashed border and muted title color (all text keeps its own passing color). | Fixed | axe `color-contrast` on `/measurements/`, `/activities/` -> 0; screenshot (dashed card, readable text) |
| UI-004 | Every plain checkbox/radio outside `.checkbox-field` (recipe meal slots, diet plan meal slots, assistant model/effort radios, API permissions, set-log flags on the workout page) | Forms | Native controls render at the browser default 13x13 px with the browser's own accent color, next to the app's styled switches and inputs. | Below the 24 px minimum target (only passing axe thanks to spacing), inconsistent look across forms and themes. | One size (1.25 rem) and the theme accent for all native checkboxes/radios. | Medium | 2.5.8 | Global rule `accent-color: var(--color-accent)` + 1.25 rem size on `input[type=checkbox|radio]`; `.checkbox-field` switches unaffected (more specific). | Fixed | screenshots (recipe form, API keys, assistant settings); axe `target-size` 0. Controls are 20 px (1.25 rem) and meet 2.5.8 through spacing + their clickable label, not size alone |
| UI-005 | Tables in `activities/activity_history.html`, `measurements/measurement_history.html`, `nutrition/recipe_detail.html`, `core/_bmi_card.html` | Tables / a11y | First or last `<th>` is empty (axe `empty-table-header`). The workout set table already uses a visually-hidden "Actions" for the same thing. | Screen-reader users get an unnamed column. | Every header has text (visually hidden where the column speaks for itself). | Low | 1.3.1 | Visually-hidden "Actions" / "Category" / "Nutrient" headers. | Fixed | axe `empty-table-header`: 6 nodes -> 0 |
| UI-006 | `base.html` - `.training-fab`, `.messages-fab` | Landmarks | The floating "continue workout" and "unread messages" buttons sit outside every landmark (axe `region` on 115 pages). | Screen-reader landmark navigation skips them; they're the app's two global shortcuts. | Inside a landmark. | Low | best practice (1.3.1) | Wrap both in one `<aside aria-label="Shortcuts">`; positioning is `fixed`, so nothing moves. | Fixed | axe `region`: 331 nodes -> 0 for the FABs; test `test_fab_sits_in_a_landmark` |
| UI-007 | `/nutrition/` "Today's plan" meal lines | Targets | Food/recipe names are a comma-separated run of 19.6 px tall links on 1.4 line-height; axe `target-size` (WCAG 2.2) fails on neighbouring links. | Hard to tap the right food on a phone. | >= 24 px spacing between adjacent targets. | Medium | 2.5.8 | Taller line-height on that list (`.inline-link-list`). | Fixed | axe `target-size` on `/nutrition/`: 8 nodes -> 0 |
| UI-008 | `workouts/session_detail.html` | Button hierarchy | "Add exercise", "Complete workout" (and the card's "Log set") are all primary. | Rulebook: one primary per section; the page's main action (finish the workout) doesn't stand out. | "Complete workout" primary, "Add exercise" secondary. | Medium | - | Class change. | Fixed | crawl `primaryButtons`; screenshot `workouts_<id>--390-dark.png` |
| UI-009 | `programs/program_detail.html` workout card | Button hierarchy | "Add exercise" and "Start workout" are both primary in one card. | Same as UI-008; starting the workout is what the card is for. | "Start workout" primary, "Add exercise" secondary (matches session detail). | Medium | - | Class change. | Fixed | crawl `primaryButtons` |
| UI-010 | Set-log form: `workouts/_performed_exercise_card.html` vs `workouts/_train_panel.html` | Consistency / forms | The same form looks different in the two places it's used: training mode shows "Warm-up"/"Failed set" as switches (`.checkbox-field`), the workout page as bare checkboxes under a label, stretched into the field grid; notes is a narrow column there. | Two looks for one action; the workout page version looks broken at 390 px. | Same field treatment in both: switches beside their label, notes full width. | Medium | - | Render the boolean fields with `.checkbox-field` and notes `.flex-full` on the workout page too. | Fixed | screenshot; test `test_set_form_matches_training_mode` |
| UI-011 | `apps/workouts/forms.py` `ExerciseSetForm` labels | Wording | Labels "reps" and "notes" are lower case while every other label ("Weight (kg)", "Target reps") is sentence case. | Visible inconsistency on the most-used form. | "Reps", "Notes" (existing translated msgids). | Low | - | Use `_("Reps")`, `_("Notes")`. | Fixed | crawl `lowercaseLabels`; test `test_set_form_matches_training_mode` |
| UI-012 | `workouts/_train_panel.html` | Headings | The current exercise's name is a bare `<h2>` inside a `.card`, so it renders as the small upper-case eyebrow - the rulebook says in-card titles are `h2.card-title`. | Long exercise names in upper case are hard to read; heading style differs from every other card. | `h2.card-title`. | Low | - | Class change. | Fixed | template review; full suite |
| UI-013 | `social/group_detail.html` | Page skeleton | "Leave group" / "Delete group" come after the "Back to groups" link. | Rulebook: the back link is the last thing on the page; a destructive button after it is easy to hit by mistake. | Destructive action above the back link. | Low | - | Move the block. | Fixed | template review; full suite (social tests) |
| UI-014 | `accounts/data_export.html` | Targets / consistency | Section `<summary>` rows are 22 px tall. | Under the 24 px minimum and the app's 44 px touch-target rule. | Summary row >= `--touch-target`. | Low | 2.5.8 | `.card > summary` gets a touch-target min-height (negative margin keeps the card size). | Fixed | crawl `smallTargets`: 27 summaries -> 0 |
| UI-015 | `accounts/data_export.html` | Wording | Section titles are raw export keys (`workout_sessions`, `custom_stretch_routine_items`) - untranslated identifiers, the only such text in the UI. | Reads as a developer view; not localized. | Human, translated titles (with the CSV file name as secondary text, since the page mirrors the export files). | Medium | 3.1.2 (language of parts) | Needs a label per section in 6 languages and a decision on whether the file names stay visible. | Deferred | - |
| UI-016 | Dashboard "This week's volume", Progress "Total volume" | Formatting | Volume prints the raw decimal: `1450.00 kg`. Other weights drop trailing zeros. | Noise in the headline figures. | `1450 kg`, `1450.5 kg`. | Low | - | `floatformat:"-1"`. | Fixed | template; full suite |
| UI-017 | `base.css` | Tokens | 154 spacing declarations use literal rem values (60 use `--space-*`); near-duplicate literal font sizes (1.1, 1.15, 1.3, 0.85 rem); `border-radius: 0.25rem` literal twice (= `--radius-sm`). | Drift: new CSS copies the nearest literal instead of the scale. | Tokens in new/changed CSS; literals only where a value is genuinely one-off. | Low | - | Literal radii -> token now; document the rule; fold the rest in as each section is touched (a big-bang rewrite would risk regressions for no visible gain). | Partly fixed | the two literal radii now `--radius-sm`; the rule is in `design-system.md` and `UI_COMPONENTS.md`; the remaining literals are left for when each section is next touched |
| UI-018 | Icons | Consistency | `core/_icon.html` (the icon registry) is used only by the nav. The month calendar inlines 13 SVGs (each icon twice: grid + legend), training mode/stretch player inline their own mute icons, and achievements/suggestions use emoji. | Icons drift in stroke and size; changes need edits in several places. | Line icons from `_icon.html`; emoji only where they are content (achievements). | Low | - | Move the calendar and mute icons into `_icon.html` (with a `class` parameter). | Deferred | - |
| UI-019 | Dashboard month calendar at >= 1024 px | Layout | Day cells stay square, so at 1280 px each is ~115 px and the calendar takes ~700 px of height. | The dashboard's least important block dominates on desktop. | Capped cell height on wide screens. | Low | - | Product/visual call; record only. | Deferred | screenshot `dashboard--1280-dark.png` |
| UI-020 | Month calendar padding days | Color | Previous/next-month days are 1.66:1 (light) / 1.94:1 (dark). | Deliberate de-emphasis of days outside the month. | - | Low | 1.4.3 | Already an accepted exception (`UI_COMPONENTS.md`, `test_accessibility.py`). | Accepted | - |
| UI-021 | `/backups/` in the live-server test environment | Environment | Returned 500 during the crawl (test settings have no backup directory); in the dev stack the page returns 200. | Not a UI defect; noted so the crawl result isn't misread. | - | - | - | None. | Accepted | dev stack: 200 |
| UI-022 | `/nutrition/foods/browse/` | Landmarks / bug | Found by the second crawl: the OpenFoodFacts attribution was included inside `{% block extra_scripts %}`, which renders in `<head>`; the browser moved the `<p>` to the top of `<body>`, outside `<main>` (axe `region`). | The attribution showed up above the page, not where the other OFF pages put it. | Inside the page content, before the back link. | Low | best practice (1.3.1) | Moved the include into `{% block content %}`. | Fixed | targeted crawl of `/nutrition/foods/browse/`: 0 axe violations |
| UI-023 | `apps/programs/forms.py`, `apps/workouts/forms.py` "exercise" field | Wording | Found by the second crawl: the exercise picker label is lower case ("exercise:") on "Add exercise" in a program and in a workout - same issue as UI-011. | Inconsistent label casing. | "Exercise". | Low | - | `_("Exercise")` (new msgid, translated in all 6 catalogs). | Fixed | full suite; `msgfmt --statistics` |
| UI-024 | `/api/keys/new/` at <= 390 px | Layout | The permission grid is ~10 px wider than the card at 390 px, so the "Delete" column header is cut at the right edge until the table is scrolled (it scrolls inside `.table-wrap` with the scroll shadow; no page overflow). | The last column looks cut off. | All four columns visible at 360 px. | Low | - | Tighter cell padding for this grid or abbreviated column headers - a visual call. | Open | screenshot `api_keys_new--390-dark.png` |

### By severity (initial)

| Severity | Count | IDs |
|---|---|---|
| Critical | 1 | UI-001 |
| High | 2 | UI-002, UI-003 |
| Medium | 6 | UI-004, UI-007, UI-008, UI-009, UI-010, UI-015 |
| Low | 11 | UI-005, UI-006, UI-011, UI-012, UI-013, UI-014, UI-016, UI-017, UI-018, UI-019, UI-020 (+ UI-022, UI-023, UI-024 found in the final audit) |
| n/a | 1 | UI-021 |

### By category

Color 3 (002, 003, 020) - Forms 3 (001, 004, 010) - Button hierarchy 2
(008, 009) - Targets 2 (007, 014) - Wording/formatting 3 (011, 015, 016)
- Structure/headings/landmarks 4 (005, 006, 012, 013) - Tokens/icons 2
(017, 018) - Layout 1 (019) - Environment 1 (021).

## Final audit - 2026-10-28

Second crawl of all 114 pages (same seed, widths, appearances and rules)
after the fixes, plus targeted re-runs of `/api/keys/new/` and
`/nutrition/foods/browse/`.

| Check | Before | After |
|---|---|---|
| axe `label` (critical) | 80 nodes | 0 |
| axe `color-contrast` (serious) | 16 nodes | 6 - all the calendar padding days (UI-020, accepted) |
| axe `target-size` (serious, WCAG 2.2) | 8 | 0 |
| axe `empty-table-header` | 6 | 0 |
| axe `region` | 331 | 0 (the 4 left after the main crawl were UI-022, fixed and re-checked) |
| Layout issues (overflow, off-screen, out-of-card, row alignment) at 320-1680 px | 0 | 0 |
| Interactive elements < 24 px (own check, stricter than WCAG: ignores spacing) | 111 | 78 - 70 native checkboxes/radios at 20 px and 8 item-name links in lists/tables (~20 px tall); all pass WCAG 2.5.8 through spacing (axe `target-size` 0) |
| Console errors on 200 pages | 0 | 0 |
| `pytest` (container) | 2225 passed, 3 skipped | 2228 passed, 4 skipped (3 new tests; the 4th skip is the opt-in crawl) |
| `pytest -m accessibility` (axe on key pages, layout at 360 px, UI behaviour) | - | 38 passed, 1 skipped |
| `ruff check .` | clean | clean |
| `msgfmt --statistics` (et, fi, it, ru, sv) | complete | complete (2 new msgids translated) |

The end-of-page viewport screenshots added to the crawl confirm the
floating buttons don't cover the last element on a phone (full-page
screenshots draw fixed elements at the first viewport's position, which
had looked like an overlap).

### Totals

24 issues: **17 fixed**, 1 partly fixed (UI-017), 1 open (UI-024),
3 deferred for a product/visual decision (UI-015, UI-018, UI-019),
2 accepted (UI-020 known exception, UI-021 test environment only).

| Severity | Found | Fixed | Remaining |
|---|---|---|---|
| Critical | 1 | 1 | - |
| High | 2 | 2 | - |
| Medium | 6 | 5 | UI-015 (deferred) |
| Low | 14 | 9 | UI-017 (partly), UI-018, UI-019 (deferred), UI-020 (accepted), UI-024 (open) |
| n/a | 1 | - | UI-021 (accepted) |

### Limitations

- Visual review by eye covered ~25 of the 114 pages; the rest are
  covered only by the automated checks. 320/768/1680 px were measured,
  not screenshotted.
- axe ran on the default palette only; the four other palettes were
  checked by computing contrast for the token pairs that changed, not by
  rendering them.
- No screen reader, real device, or 200/400 % zoom testing was done.
  Signed-out pages were rendered while signed in.
- Hover/active/disabled/loading states were reviewed in CSS and through
  the existing behaviour tests, not driven per component in the crawl.
- Automated checks find a subset of WCAG failures; this is not a claim
  of WCAG 2.2 AA conformance.

### Next steps

1. UI-015: decide on human titles for the data-export sections (and
   whether the CSV file names stay visible), then translate them.
2. UI-024: tighten the API key grid so all four columns fit at 360 px.
3. UI-018: move the calendar/timer icons into `core/_icon.html`.
4. UI-019: decide on the desktop calendar cell height.
5. A manual pass with VoiceOver/TalkBack on the workout logging flow,
   the most-used screens.
