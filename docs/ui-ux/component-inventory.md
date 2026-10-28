# Component inventory

Reusable UI pieces, where they live and where they're used (counts are
template files, 2026-10-28). Usage rules: `docs/UI_COMPONENTS.md`.
Values: [design-system.md](design-system.md).

## Layout and navigation

| Component | Source | Used in | Variants / states | Notes |
|---|---|---|---|---|
| Page shell | `templates/base.html` | every signed-in page | theme/appearance attrs, `has-fab` | skip link, toasts, nav, `<main>`, FABs |
| Bottom / top nav | `base.html` `.bottom-nav` + `core/_icon.html` | global | icon-only < 768 px, icon + label above; `aria-current`; unread dot | |
| Page title bar | `.top-bar` + `.top-bar-actions` | 125 / 31 | - | title only; actions in the sibling |
| Breadcrumbs | `core/_breadcrumbs.html` | 15 | - | pages two+ levels deep |
| Sub-nav | `nutrition/_subnav.html` (`.nutrition-subnav`) | nutrition pages | `aria-current` | horizontally scrolling pills |
| Range filter | `.range-filter`, `analytics/_range_filter.html` | 4 | `aria-current` | segmented pills |
| Settings row | `.settings-row` + `core/_chevron.html` | profile (3 files) | hover/focus border | |
| FABs | `.training-fab`, `.messages-fab` (base.html) | global | live pulse dot | wrapped in an `<aside>` landmark (UI-006) |
| Signed-out brand | `registration/_auth_brand.html` | login, signup, 2FA | - | replaces `.top-bar` |

## Content

| Component | Source | Used in | Variants | Notes |
|---|---|---|---|---|
| Card | `.card` | 146 | `.card-link` (15), `.card-untracked`, `.danger-zone` (4), `details.card` | |
| Action row | `.card-action-row` | 23 | text + 1..n actions | enforced by `test_layout.py` |
| Stat row / figure | `dl.stat-row`, `.stat-figure`, `.stat-value(-lg)` | 10 / 7 | - | monospace numbers |
| Tag | `.tag` | 39 | `-success` 7, `-muted` 19, `-outline` 6, `abbr.tag` | text always present |
| Table | `.table-wrap > table.set-table` | 20 / 19 | `.list-table` (1), `.bar-chart-table` | scroll shadow on phones |
| Charts | `core/_chart.html`, `core/_bar_chart.html` | 4 / 5 | `tour_anchor=` | SVG + data table fallback |
| Month calendar | `nutrition/_month_calendar.html` | dashboard, nutrition, stretching | outside day, today, popover | inline icons (UI-018) |
| Achievement carousel | dashboard + `static/js/dashboard-carousel.js` | dashboard | paused / playing | |

## Actions

| Component | Source | Used in | Variants | Notes |
|---|---|---|---|---|
| Button | `button`, `a.button` | everywhere | primary, `.button-secondary` (60), `.button-danger` (29), `.button-link` (102) | hover/active filters, disabled = attribute |
| Row buttons | `.set-actions` | 7 | compact (mouse) / 44 px (touch) | |
| Help button | `.help-button` | 3 | - | opens a modal |
| Confirm dialog | `static/js/confirm-dialog.js` via `data-confirm` / `hx-confirm` | 29 | danger confirm | replaces `confirm()` |
| Confirm page | `programs/program_confirm_delete.html`, `core/backup_restore_confirm.html`, `accounts/account_delete.html` | 3 | - | most severe actions only |

## Forms

| Component | Source | Used in | Variants | Notes |
|---|---|---|---|---|
| Field | `core/_field.html` | 67 | text/select/textarea, checkbox, checkbox list | label, help, linked error, `aria-invalid` |
| Form errors | `core/_form_errors.html` | 59 | - | `.alert-error` summary |
| Switch | `.checkbox-field` | 4 + set-log forms | on/off | real checkbox with `appearance: none` |
| Native checkbox / radio | global `input[type=checkbox|radio]` | recipe/diet-plan meal slots, assistant settings, API permissions | - | 1.25 rem, theme accent (UI-004) |
| Compact set form | `.set-log-form` / `.set-field` | `workouts/_performed_exercise_card.html`, `workouts/_train_panel.html` | train panel hides the extras behind "More options" | the one accepted exception to `_field.html`; boolean fields are switches in both (UI-010) |
| Searchable picker | `apps.core.widgets.SearchablePickerWidget`, `core/widgets/searchable_picker.html`, `static/js/searchable-picker.js` | coaching share forms | - | checkboxes stay the no-JS fallback |
| Live search | `hx-get` + `core/_search_indicator.html` / `_search_status.html` | 8 / 6 | - | |
| Permission matrix | `api/key_form.html` | 1 | - | table of checkboxes; each named "<action> - <context>" (UI-001) |

## Feedback and states

| Component | Source | Used in | Notes |
|---|---|---|---|
| Empty state | `core/_empty_state.html` | 37 (+ documented hand-written exceptions) | message, hint, CTA |
| Alert | `.alert` + `-info/-warning/-success/-error` | 14 | in-page |
| Toast | `.pr-banner` in `#pr-toast-container` (base.html), `records/_pr_toasts.html` | global | success auto-dismiss 6 s; errors stay |
| HTMX error toast | `static/js/htmx-errors.js` | global | |
| Loading | `static/js/page-loading.js`, `.htmx-request`, `.spinner` | global / 5 | |
| Focus after swap | `static/js/htmx-focus.js`, `data-autofocus` | global | |
| Background task status | `core/_backup_task_status.html` | 2 | polling `.alert` |
| Pagination | `core/_pagination.html` (+ `_pagination_nav.html`) | 11 | prev/next both secondary |

## Overlays

| Component | Source | Used in | Notes |
|---|---|---|---|
| Modal | `.modal-backdrop > .modal-card` + `static/js/modal-a11y.js` | 7 templates (profile, privacy notice, API help, barcode scan, calendar legend, exercise guide, onboarding) | focus trap/return, Escape, `.modal-card-wide` |
| Guided tour | `static/js/tutorial.js`, `apps/tutorials/tours.py` | every page | `data-tour` anchors |
| Abbreviation tooltip | `abbr[title][tabindex=0]` (CSS only) | jargon everywhere | hover + focus |
| Install prompt | `core/_pwa_install_prompt.html`, `static/js/pwa-install-prompt.js` | global | |

## Overlaps and intentional differences

- **Set-log form** exists twice (workout page, training mode). Same
  fields, same field treatment; training mode deliberately collapses
  the extras to keep logging to two inputs + one tap. Not merged into
  one partial because the two post to different endpoints and swap
  different targets.
- **`.empty-state`** doubles as a neutral centered notice (403/404,
  calculators) - documented in `UI_COMPONENTS.md`.
- **Data export `<details>`** uses a plain `summary` rather than the
  `details.preferences` pattern: it's a long list of data sections, not
  a settings group, so no chevron row styling. Its summary is sized to
  the touch target like the others (UI-014).
- **Icons**: `_icon.html` is the registry, but the calendar and timer
  icons are still inline (UI-018, deferred).
- **500.html** is standalone (no context processors) - accepted.
