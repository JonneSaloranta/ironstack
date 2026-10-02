# Tutorials (guided page tours)

`apps.tutorials` shows each page's short guided tour the first time a user
opens that page: the element being explained is highlighted in its own shape
(an outline hugging it, with its own corner rounding) while the rest of the
page is dimmed (not hidden), and a compact card explaining it sits beside it —
below or above, wherever there's room, never on top of it. The page scrolls
so both fit. This document is the maintenance guide — read it before
changing a tour, a toured template, or adding a page.

## Pieces

| Piece | Where | What it does |
|---|---|---|
| Tours | `apps/tutorials/tours.py` → `TOURS` | every tour, in profile → Tutorials order |
| Shapes | `apps/tutorials/registry.py` → `Tour`, `Step` | `Step(anchor, title, body, optional=False)` |
| Anchors | templates, `data-tour="<anchor>"` | marks the element a step highlights |
| Exemptions | `tours.py` → `NO_TOUR_NEEDED` | argument-less pages with no tour, and why |
| Which tour runs | `services.tour_to_show` + `context_processors.tutorial` | JSON for the page, rendered by `base.html` |
| Player | `static/js/tutorial.js`, CSS "Guided page tours" in `base.css` | highlight, card placement, keyboard, completion |
| State | `TutorialCompletion` (per user, per tour key), `User.tutorials_enabled` | seen tours; automatic tours on/off |
| Profile page | `tutorials:list` (`templates/tutorials/tutorial_list.html`) | Start / Show again / Show all again / on-off |
| Guards | `apps/tutorials/tests.py` | see "What the tests enforce" |

## When a tour runs

- Automatically on a page that has one, the first time the user opens it —
  unless they've switched tutorials off, haven't finished onboarding yet (one
  overlay at a time), or have already finished or skipped that tour.
- Always with `?tour=<key>` (profile → Tutorials → "Start").
- Steps whose element isn't on the page (or is hidden) are skipped by the
  player, so a tour adapts to what the user actually has.
- **Done** or **Skip tour** (or Escape) marks it seen. **Don't show
  tutorials** marks it seen and turns automatic tours off; profile →
  Tutorials turns them back on, and "Show again" re-arms one tour.

## How deep a tour goes

Match the page, not a quota:

- Pages people log on (a workout, training mode, the food diary, adding
  food): one step per thing they'll touch — several steps.
- Overview and list pages: what's there and the one or two actions — one to
  three steps.
- Plain forms, single calculators, simple lists covered by their parent's
  tour: no tour — add them to `NO_TOUR_NEEDED` with the reason.

Keep each step's text to one or two short sentences that say what the thing
is for, not how HTML works. Mention user control where it matters (a
suggestion is only a suggestion; editing a program never changes logged
history).

## Recipes

### Change a tour's text

Edit the step in `tours.py`, then update the translations (see
"Translations"). The translation test fails until every language has it.

### Change a template that a tour points into

Keep the `data-tour` attribute on whichever element now plays that role. If
the element is removed, remove or re-point the step. The anchor test fails
when a required anchor disappears from its page.

### Add a step

1. Put `data-tour="<new-anchor>"` on the element. Anchor names are
   kebab-case and describe the element (`food-search`), not the page layout.
   For a chart, pass `tour_anchor="<anchor>"` to `core/_chart.html` /
   `core/_bar_chart.html`.
2. Add `Step("<new-anchor>", _("Title"), _("Body"))`. Use `optional=True`
   only if the element is legitimately absent sometimes (an empty list, a
   staff-only control, an in-progress-only button).
3. Translate.

### Add a page

Every page without URL arguments needs a tour **or** a `NO_TOUR_NEEDED`
entry — the coverage test lists offenders. For a page with arguments (a
detail page), add a tour if it's worth one and give it a `start_url`
(`first_url(<queryset for user>, "<url name>")`) so profile → Tutorials can
open it.

### Add a tour

Add a `Tour(...)` next to the others of its section (sections must stay
contiguous). The `key` is stored in the database: never rename a shipped one
(that would show it to everyone again). Every tour needs at least one
non-optional step.

## Translations

All tour texts use `gettext_lazy`. After changing them, regenerate and
translate the catalogs (docs/ARCHITECTURE.md "Regenerating the catalogs"),
for all of en/fi/sv/et/it/ru. A word that's genuinely spelled the same in
another language (a loanword) goes in `SAME_IN_TRANSLATION` in the tests.
Button labels use the `tutorial` context (`pgettext("tutorial", "Next")`),
because some bare msgids are already taken by other meanings ("Back" is also
a muscle group).

## What the tests enforce

`apps/tutorials/tests.py`:

- **Anchors** — every tour's page is rendered for a user with data on every
  page; each required step's `data-tour` must be in the HTML.
- **Coverage** — every argument-less page that renders the app shell (signed
  in, even as staff) has a tour or a `NO_TOUR_NEEDED` entry; every
  `NO_TOUR_NEEDED` name is a real URL and has no tour.
- **Translations** — every tour title, section and step text differs from the
  English in every other language.
- **Registry** — unique keys, at most one tour per page, contiguous sections,
  a required step in every tour.
- **Behaviour** — runs once, not during onboarding, not when switched off,
  `?tour=` forces it, completion/disable/reset endpoints.

The player itself (`tutorial.js`) has no unit test; check it in a browser
after changing it (mobile width, a step near the top and the bottom, the
bottom nav itself, a tall element; the card must never cover the highlight;
Escape, keyboard Tab/arrows, reduced motion).
