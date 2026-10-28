# UI/UX implementation plan

From the findings in [audit.md](audit.md). Shared components and root
causes first, then single pages. Every stage ends with the full
`pytest` suite, the accessibility/layout browser tests and a re-run of
the audit crawl (`apps/core/test_ui_audit_crawl.py`).

## Stage 1 - Baseline and inventory (done)

- Scope: route map, crawl at 5 widths x 2 appearances, axe, screenshots,
  static checks; the four documents in this folder.
- Outcome: 21 findings, baseline suite 2225 passed / 3 skipped.

## Stage 2 - Shared tokens and components (CSS) - done

| Scope | Issues | Files |
|---|---|---|
| Native checkbox/radio size + accent | UI-004 (and the size half of UI-001) | `base.css` |
| Muted tag tint | UI-002 | `base.css` |
| Untracked card without opacity | UI-003 | `base.css` |
| Summary touch target | UI-014 | `base.css` |
| Inline link list spacing | UI-007 | `base.css`, `nutrition/dashboard.html` |
| Literal radii -> tokens | UI-017 (part) | `base.css` |

- Dependencies: none.
- Regression risk: the checkbox rule is global - `.checkbox-field`
  switches, the shopping list's checkboxes and the toggle switch must be
  unaffected (more specific selectors); the tag tint must pass in all 5
  palettes x 2 appearances (computed, not assumed).
- Verify: axe on the affected pages (light + dark), screenshots of
  recipe form, assistant settings, shopping list, profile preferences,
  measurements list; contrast script over every palette.

## Stage 3 - Shared templates and accessibility semantics - done

| Scope | Issues | Files |
|---|---|---|
| Permission matrix names | UI-001 | `api/key_form.html` (+ form field data if needed) |
| Empty table headers | UI-005 | 4 templates, `.po` for one new msgid ("Nutrient") |
| FAB landmark | UI-006 | `base.html` |

- Regression risk: `test_ui_behaviour.py` / tour anchors on the FABs
  (`data-tour` must stay on the same elements); API key create flow.
- Verify: axe `label`, `empty-table-header`, `region`; API key tests.

## Stage 4 - Page rollout - done

| Scope | Issues | Files |
|---|---|---|
| Button hierarchy on workout page / program card | UI-008, UI-009 | `workouts/session_detail.html`, `programs/program_detail.html` |
| Set-log form parity + label casing | UI-010, UI-011 | `workouts/_performed_exercise_card.html`, `apps/workouts/forms.py` |
| In-card heading in training mode | UI-012 | `workouts/_train_panel.html` |
| Back link last on group page | UI-013 | `social/group_detail.html` |
| Volume formatting | UI-016 | `core/dashboard.html`, `analytics/dashboard.html` |

- Regression risk: tests asserting button markup or label text
  ("reps"); tours pointing at moved elements.
- Verify: full suite, screenshots of each page.

## Stage 5 - Regression testing - done

- `docker compose exec -T web pytest` (full suite), `ruff check .`.
- Browser suite: `pytest -m accessibility` (axe on key pages, layout at
  360 px, UI behaviour) then `--create-db` for the container.
- Re-run the audit crawl; diff the axe/overflow results against the
  baseline.

## Stage 6 - Final audit - done (results in audit.md, "Final audit")

Update every row's status in `audit.md` with the verification used;
add anything new found by the second crawl.

## Deferred (needs a decision or a larger change)

| Issue | What's needed |
|---|---|
| UI-015 data export titles | A translated title per export section (28 keys x 6 languages) and a decision whether the CSV file name stays visible as secondary text. |
| UI-018 icon registry | Move calendar/timer SVGs into `core/_icon.html` with a size/class parameter; visual diff of the calendar in all themes. |
| UI-019 desktop calendar | Visual decision on cell height above 1024 px. |
| UI-024 API key grid width | Fit four columns at 360 px (padding or abbreviated headers). |
| UI-017 remaining literals | Fold literal spacing/font sizes into tokens section by section when those sections are next touched, not as one sweeping change. |
