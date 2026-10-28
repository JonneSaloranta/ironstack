# Design system

What the app's visual language is made of and why. Values here are
read from `static/css/base.css` (the only stylesheet; `admin_theme.css`
re-skins the Django admin with the same tokens). **`base.css` `:root` is
the source of truth for values; `docs/UI_COMPONENTS.md` is the source of
truth for which markup/class to use.** This file explains the two and
must not grow a third set of rules - when a value changes, change it in
`base.css` and update the table here.

## Principles

1. **A training log first.** Logged numbers are data: monospace with
   tabular numerals (`--font-mono`, `.stat-figure`), never decorative.
2. **Mobile first.** Designed at 360-430 px; wider screens get a wider
   column and a top nav, not a different app.
3. **One look per job.** Same purpose -> same component (one primary per
   section, one empty state, one confirm dialog, one pager).
4. **Theme-agnostic CSS.** Every color comes from a `--color-*` role, so
   5 palettes x light/dark are one block of values each, not parallel
   stylesheets. Hover/active are brightness filters, not hand-picked
   colors.
5. **Accessible by default.** WCAG 2.2 AA as the bar, checked by axe in
   the browser test suite; touch targets 44 px on touch devices.
6. **Server-rendered, progressively enhanced.** Every control works as a
   plain form/link; HTMX and Alpine only make it quicker.

## Color roles

Declared once each as `light-dark(<light>, <dark>)`. `color-scheme`
picks the half: device preference (Auto) or `data-appearance` on
`<html>`. Palettes: default, `nordic`, `vaporwave`, `earth`, `zen`
(`[data-theme]`).

| Token | Role | Default light / dark |
|---|---|---|
| `--color-bg` | page background, input fill | `#f2f3f5` / `#101317` |
| `--color-surface` | cards, modals, nav | `#ffffff` / `#181c22` |
| `--color-border` | borders, dividers, switch track | `#d7dbe0` / `#2a2f37` |
| `--color-text` | body text | `#14171a` / `#e7eaee` |
| `--color-text-muted` | secondary text, eyebrow headings | `#5b6570` / `#9aa3ad` |
| `--color-accent` | primary button, links, current nav item, focus ring | `#2563eb` / `#5b9dfd` |
| `--color-on-accent` | text on accent/danger fills | `#ffffff` / page bg |
| `--color-danger` | destructive buttons, errors | `#c0311d` / `#e5636a` |
| `--color-success` | success tags/alerts, adherence | `#187a41` / `#4fb477` |
| `--color-warning` | warnings | `#8f6208` / `#e0a94f` |
| `--color-gold` | PRs (and only PRs) | `#7d5a14` / `#d4af37` |
| `--color-info` | info alert edge | = accent |

Rules:

- Status is never color alone: tags have text, alerts have a heading
  or text, calendar icons have a legend.
- Fixed, theme-independent colors are allowed only for: modal/tour
  backdrops, the barcode scanner's video background, Nutri-Score/NOVA
  grade badges (a published scale), white on a switch knob.
- Tinted fills (e.g. `.tag-muted`) are derived with `color-mix()` from a
  role, never a new hex value, so every theme gets them for free.

## Typography

| Token | Size | Use |
|---|---|---|
| `--text-xs` | 0.75 rem | tags, chart axes, fine print |
| `--text-sm` | 0.875 rem | secondary text, compact forms |
| `--text-base` | 1 rem | body, inputs (16 px stops iOS zoom-on-focus) |
| `--text-lg` | 1.25 rem | - |
| `--text-xl` | 1.5 rem | `.stat-value` headline figures |

- Body: `system-ui` stack, line-height 1.4. No webfonts (everything is
  self-hosted, see `docs/SECURITY.md`).
- Numbers: `--font-mono` + `font-variant-numeric: tabular-nums`
  (`.stat-figure`).
- Headings: `h1` page title (weight 800, -0.01em) - exactly one per page,
  in `.top-bar`. `h2` has two styles only: the muted upper-case eyebrow
  between cards, and `h2.card-title` (1.15 rem, text color) inside a card.
- Display sizes that are genuinely one-off (the stretch player's clock,
  3.5 rem; the auth brand, 2 rem) stay literal with a comment.

## Spacing

`--space-1..6` = 0.25, 0.5, 0.75, 1, 1.5, 2 rem. Cards pad 1 rem and sit
1 rem apart; the page column pads 1 rem (2 rem from 768 px). New or
changed CSS uses the scale; a literal needs a reason (an optical
adjustment like `0.1rem` tag padding is fine).

## Layout and breakpoints

| Range | Container | Navigation |
|---|---|---|
| < 768 px | `max-width: 30rem`, padding 1 rem | fixed bottom bar, icon-only, 4.25 rem tall |
| 768-1023 px | `48rem`, padding 2 rem | top bar, icon + label |
| >= 1024 px | `64rem` | top bar, icon + label |

Only these two breakpoints (`768px`, `1024px`) exist; use them, and
write mobile-first `min-width` queries. Plus `(pointer: coarse)` for
touch-size targets and `(hover: hover) and (pointer: fine)` for
hover-only reveals. Safe areas: anything fixed to the top uses
`env(safe-area-inset-top)`.

## Shape and depth

- Radius: `--radius-sm` 0.25 rem (code, small chips), `--radius` 0.5 rem
  (cards, inputs, buttons), `--radius-lg` 0.75 rem (modals, tour card),
  `999px` pills (tags, range filter), `50%` round buttons/dots.
- Borders: 1 px `--color-border`; 4 px left edge for `.alert` variants;
  dashed border = "nothing here yet" (untracked type, see UI-003).
- Shadows only on things that float: `--shadow-sm/md` (toasts, cards
  that lift), `--shadow-fab`, `--shadow-popover`, `--shadow-nav`.

## Stacking (z-index)

`--z-raised` 20 < `--z-popover` 30 < `--z-subnav` 40 < `--z-nav` 50 <
`--z-overlay` 80 < `--z-fab-secondary` 85 < `--z-fab` 90 < `--z-skip`
100 < `--z-toast` 200 < `--z-modal` 300 < `--z-loading` 500 (< tour).
Never a literal z-index.

## Iconography

- Line icons, 24 px grid, `stroke="currentColor"`, stroke-width 2
  (Feather style), from `templates/core/_icon.html`; decorative icons
  are `aria-hidden`, icon-only buttons carry an `aria-label`.
- Emoji only as content (achievement badges, the suggestion bulb),
  always `aria-hidden` with the meaning in text.
- Known debt: calendar and timer icons are still inline (audit UI-018).

## Components

Usage rules for every component (buttons, forms, cards, rows, tables,
tags, alerts, modals, empty/loading/error states, pagination, tours)
are in `docs/UI_COMPONENTS.md`; the list of where each lives and is
used is [component-inventory.md](component-inventory.md). Decisions
made in this audit:

- **Buttons**: primary = the one action a section exists for. On the
  workout page and in a program's workout card that is
  "Complete workout" / "Start workout"; "Add exercise" is secondary.
- **Checkboxes**: an on/off *setting* is a switch (`.checkbox-field`);
  one option among several (meal slots, API permissions) or a radio is
  a native control at 1.25 rem in the theme accent (`accent-color`).
  The set-log form uses switches in both of its places.
- **Muted tag**: a tinted fill (`color-mix` of border and surface) so
  muted text keeps >= 4.5:1 in all palettes.
- **De-emphasis** never uses `opacity` on text (it silently breaks
  contrast); use muted color or a dashed border instead. The month
  calendar's padding days are the one accepted exception.

## Interaction states

| State | Rule |
|---|---|
| Hover / active | `filter: brightness(1.1 / 0.92)` on buttons |
| Focus | `:focus-visible` 2 px accent outline, 2 px offset, everywhere |
| Disabled | `disabled` attribute -> 50 % opacity + `not-allowed` (never color alone) |
| Selected / current | `aria-current="page"` (nav, subnav, range filter) styled from the attribute |
| Loading | top loading bar on every navigation/user-started HTMX request, `.htmx-request` dimming, `hx-disabled-elt` on submit buttons |
| Success | toast (`messages.success`) auto-dismissing after 6 s, paused on hover/focus |
| Error | field: `.field-error` + `aria-invalid`; form: `.alert-error` summary; HTMX failure: persistent error toast |
| Empty | `core/_empty_state.html` with a next step |

## Motion

All transitions/animations are cut by one `prefers-reduced-motion`
rule at the end of `base.css` (Alpine `x-transition` included).

## Accessibility requirements

WCAG 2.2 AA: contrast 4.5:1 text / 3:1 UI, targets >= 24 px (44 px on
touch), visible focus, one `<main>`, labelled landmarks, every control
named, jargon abbreviations in `<abbr tabindex="0">`. Enforced by
`apps/core/test_accessibility.py` (axe) and `test_layout.py` (overflow)
in the `accessibility` CI job.
