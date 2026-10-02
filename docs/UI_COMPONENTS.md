# UI component reference

The rulebook for building or changing any template, so every page looks and
behaves the same. `docs/UI.md` explains *why* things are the way they are
(history, regressions, product reasoning); this file says *what to use*.
Read this before touching `templates/` or `static/css/base.css`.

When a page needs something this file doesn't cover, extend an existing
pattern (and add it here) rather than inventing a one-off. If a page
deliberately breaks a rule, list it under "Accepted exceptions" below.

## Page skeleton

Every signed-in page extends `base.html` and follows this order:

```django
{% extends "base.html" %}
{% load i18n %}
{% block title %}{% trans "Page name" %} — IronStack{% endblock %}
{% block content %}
{% include "core/_breadcrumbs.html" with ... %}   {# only on pages two+ levels deep #}
<div class="top-bar">
  <h1 class="no-margin">{% trans "Page name" %}</h1>
</div>
<div class="top-bar-actions">…</div>              {# optional; a sibling right after .top-bar, never inside it #}

… sections / cards …

<p><a class="button-link" href="…">&larr; {% trans "Back to X" %}</a></p>
{% endblock %}
```

- `<title>` is always `<Page name> — IronStack`.
- The `<h1>` lives in `.top-bar`, and that's the only `<h1>` on the page.
- `.top-bar` holds only the title. `.top-bar-actions`, directly after it,
  holds status tags (`.tag`) and the page's own entry points:
  - list pages: **"New"** as primary `a.button` (plain "New" — the page title
    already says what), then secondary extras like "Import";
  - detail pages: **"Edit"** as `.button-secondary` (editing isn't what a
    detail page is for), plus other secondary actions (Export, …).
- The bottom "Back to X" link is the last thing on the page. Exceptions:
  chat threads (`message_thread`, `group_thread`), which put the back link in
  the top bar because the message composer sits at the bottom.
- Signed-out pages (login, signup, 2FA verify) use
  `registration/_auth_brand.html` instead of a `.top-bar`.

## Headings

There are exactly two `<h2>` styles:

| Use | Markup | Looks like |
|---|---|---|
| Section label **between** cards | `<h2>Section</h2>` | small, muted, uppercase "eyebrow" |
| Title **inside** a card | `<h2 class="card-title">Title</h2>` | normal text color, 1.15rem |

- Don't put a bare eyebrow `<h2>` inside a `.card`.
- `<h3>` only below an `<h2>` in the same container (modals, the API help).
- Modal titles: `<h2 class="no-margin">` inside `.modal-header`.
- Every card that has a title uses `h2.card-title` — also result cards,
  meal cards and settings cards. Extra context after the title goes in a
  `<span class="text-muted">` inside the `<h2>` or a following `<p>`.
- `<strong>` is for a **list row's item name** (a recipe, a key, a member)
  and for the headline inside an `a.card.card-link` nudge — never a card's
  section title.

## Cards and rows

| Pattern | Class | When |
|---|---|---|
| Plain content box | `.card` | the default container for anything |
| Whole card is one link | `a.card.card-link` | a nudge/shortcut with one destination (dashboard's "Continue workout", body-tracking reminder) |
| Text + actions on one row | `.card-action-row` | a list row or card with its own button(s): text first, actions last |
| Settings-list row | `.settings-row` (+ `core/_chevron.html`) | profile-style navigation lists |
| Item name linking to its detail page | `.inline-item-link` | a food/recipe/exercise name inside a row |
| Key figures | `<dl class="stat-row">` + `.stat-figure` on numbers | 2–4 numbers side by side |
| Big single number | `.stat-value` / `.stat-value-lg` | one headline figure |
| Admin/destructive section | `.danger-zone` | staff-only or account-level dangerous actions |
| Sub-group label in a long form | `.field-group-label` | profile preferences |
| Collapsible section | `<details class="card preferences">` + `<summary><h2 class="card-title no-margin">` (add `open` when its form has errors) | profile preferences, diary quick entry |

Numbers that should line up (weights, reps, macros, dates in tables) get
`.stat-figure` (monospace, tabular numerals).

## Buttons

Hierarchy (base.css "Buttons: states + one clear hierarchy"):

| Class | Meaning | Examples |
|---|---|---|
| bare `<button>` / `a.button` | **primary** — the one action the screen exists for | Save, Log set, Start workout |
| `.button-secondary` | any other action | Edit (in a row), Import, Export, Full view, ←/→ icon arrows |
| `.button-danger` | destroys or ends something | Delete, Remove, Deactivate, Abandon, Revoke, Leave group |
| `.button-link` | navigation away, not an action | ← Back to X, Cancel |

- At most one primary button per form/screen section.
- No `+` prefix in labels; use text ("New", "Add …").
- Disabled: the `disabled` attribute (or `aria-disabled="true"` on an `<a>`);
  never fake it with color.
- Hover/active states come from `filter` — never hand-pick per-theme colors.
- Icon-only buttons need an `aria-label`. Icons come from
  `core/_icon.html` (add an `elif` for a new one); close buttons use
  `&times;` with `aria-label="Close"` (`.modal-close` / `.pr-toast-close`).
- Row-level buttons sit in `.set-actions` (compact on mouse, 44px on touch).

### Wording (see `docs/UI.md` "Wording glossary")

- **New X** creates a top-level thing; **Add X** puts something into a container.
- **Delete** destroys a user's record; **Remove** detaches from a container;
  **Deactivate** retires a library item that history still points at.
- **Log X** records a measurement/activity/set/food.
- **Save** submits an edit/create form.
- Back links say **"← Back to <place>"**; cancel links say **"← Cancel"**.
  Every create/edit form page ends with one (edit → the object, create → the list).
- The page a nav tab opens uses the tab's name.

## Destructive actions

- Every destructive POST form carries a confirmation:
  `data-confirm="{% filter force_escape %}{% trans "Delete this X?" %}{% endfilter %}"`
  (or `hx-confirm`). `static/js/confirm-dialog.js` renders it. Never call
  `confirm()` in Alpine/inline JS.
- A separate confirm *page* (`*_confirm_delete.html`, `account_delete.html`,
  `backup_restore_confirm.html`) replaces `data-confirm` for the most severe
  actions; its submit is `.button-danger` and it has a Cancel `.button-link`.
- Deletion of a whole object lives on its detail page, not on list rows.

## Forms

```django
<form class="card" method="post">
  {% csrf_token %}
  {% include "core/_form_errors.html" with form=form %}
  {% for field in form %}{% include "core/_field.html" with field=field %}{% endfor %}
  <button type="submit">{% trans "Save" %}</button>
</form>
<p><a class="button-link" href="…">&larr; {% trans "Back to X" %}</a></p>
```

- Always `core/_field.html` (label, help, linked errors, checkbox layout) and
  `core/_form_errors.html` at the top. Never `form.as_p` etc.
- Hand-rolled inputs outside that loop need a `<label>` or `aria-label`.
- Compact inline forms (set logging, training mode, diary quantity) use
  `.set-field`/`.set-log-form`/`.qty-input` — the only allowed exception to
  `_field.html`.
- Live search: `hx-get` + `hx-trigger="input changed delay:…"` with
  `hx-indicator` pointing at `core/_search_indicator.html`.

## Lists, tables, pagination

- Tables: `<div class="table-wrap"><table class="set-table">…</table></div>`.
  Bar-chart data tables use `.bar-chart-table`.
- Pagination: always `{% include "core/_pagination.html" %}` (keeps filters
  via `url_replace`). Optional `page=` / `param=` for a second pager on the
  same page, `anchor=` to land back on the pager after a full page load,
  `label=` for its aria-label. Previous and Next are both
  `.button-secondary`, like every other pair of prev/next arrows (calendar
  months, diary days, training-mode exercises) — the two sides of one
  control always look the same.
- Status/metadata pills: `.tag` (accent), `.tag-success`, `.tag-muted`,
  `.tag-outline`; never rely on color alone — the tag has text.
- Filters: `.filter-row`; date ranges: `.range-filter` (segmented control).

## States

| State | Use |
|---|---|
| Empty | `{% include "core/_empty_state.html" with message=_("…") hint=_("…") cta_url=… cta_label=_("…") %}` (build the URL first with `{% url … as empty_cta_url %}`) |
| Loading | automatic `.htmx-request` dimming; `.spinner` / `core/_search_indicator.html` for slow ones |
| Field error | `_field.html` (`.field-error`) |
| Form error | `_form_errors.html` (`.alert.alert-error`) |
| In-page notice | `.alert` + `.alert-info/-warning/-success/-error` |
| Success / feedback | `messages.success(...)` in the view → toast (`.pr-banner`), auto-dismisses |
| HTMX failure | handled globally by `static/js/htmx-errors.js` |

## Modals

`.modal-backdrop` > `.modal-card` (`.modal-card-wide` for long content) >
`.modal-header` (`<h2 class="no-margin">` + `.modal-close`) + `.modal-body`.
Alpine `x-show`; focus trap/return comes free from `static/js/modal-a11y.js`.
Help/info modals open from a round `.help-button`.

## Guided tours

Every page's tour is in `apps/tutorials/tours.py` (docs/TUTORIALS.md). Its
steps point at `data-tour="<anchor>"` attributes: keep them on the element
that plays that role when you restyle a template, put one on anything new a
tour should explain, and pass `tour_anchor=` to the chart partials. Never
select tour targets by CSS class.

## Styling rules

- No `style=""` attributes, except a value that is genuinely data-driven
  (progress bar width).
- Colors only through `--color-*`; spacing `--space-1…6`; type `--text-xs…xl`;
  radii `--radius-sm/--radius/--radius-lg`; shadows `--shadow-*`; stacking
  `--z-*`. Reach for an existing utility (`.row`, `.stack`, `.mt-*`,
  `.text-muted`, `.text-danger`, `.flex-full`, …) before writing new CSS.
- Touch targets ≥ `--touch-target` (2.75rem).
- Animations respect `prefers-reduced-motion`.
- Jargon abbreviations get `<abbr tabindex="0" title="…">` (PR, 1RM, RPE, BMI).
- All user-facing text goes through `{% trans %}`/`{% blocktrans %}`; multi-line
  template comments use `{% comment %}`.

## Accepted exceptions

Places that knowingly differ from the rules above:

- Chat threads put the back link in the top bar (composer is at the bottom).
- Training mode (`_train_panel.html`) and the set-log form render fields
  compactly without `_field.html`.
- `500.html` is standalone (no context processors), so it can't use the shared
  partials or `{% url %}`.
- Signed-out pages have no `.top-bar`.
- Empty states whose message contains markup (`<abbr>PRs</abbr>`), contains a
  form (dashboard's "Start freeform workout"), or is an `<li>` inside a list
  (`routine_detail.html`) stay hand-written `<div class="card empty-state">`.
- `.empty-state` is also used as a neutral centered notice where nothing is
  "empty": 403/404, invalid group invite, calculator "can't compute" results,
  nutrition target suggestions, training mode's "all done".

## Audit log

### 2026-10-01 — first full template audit

Found consistent: no stray inline styles, every back link uses
`.button-link` with `&larr;`, no `confirm()` calls, no `form.as_p`, every
table wrapped, no `+`-prefixed labels.

Fixed after the owner's decisions:

1. Card section titles: ~25 `<strong>` titles and 3 in-card eyebrow `<h2>`s
   → `h2.card-title`.
2. Top-bar buttons: "New" → primary, plain "New" everywhere (nutrition,
   groups); "Edit" → secondary everywhere (program, exercise). Program
   list's actions moved into `.top-bar-actions`.
3. 32 hand-written empty states → `core/_empty_state.html`.
4. Stretch-in-routine "Remove" got a confirmation; exercise image "Remove"
   → `.button-danger`; stretch/routine retirement relabelled "Deactivate";
   social back links → "Back to …"; every Cancel has `&larr;`; the month
   calendar's and the pager's "previous" arrows → `.button-secondary` to
   match their "next" arrows (owner-reported); exercise and
   program forms got back links; recipe and food list pagers → the shared
   partial (also fixes their un-encoded search terms).
