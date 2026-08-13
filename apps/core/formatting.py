"""Shared presentation helpers — apps.core, matching its role as the home
for cross-cutting display formatting (see apps.core.units, apps.core.charts,
apps.core.templatetags.core_extras).
"""

from django.utils.functional import lazy
from django.utils.html import format_html
from django.utils.safestring import SafeString
from django.utils.translation import gettext_lazy as _

# Canonical English wording for each abbreviation's expansion — reused
# everywhere the abbreviation appears (form labels below, and templates
# via `{% trans "..." as var %}` with this exact same source text) so
# they all collapse into one translation catalog entry instead of
# fragmenting into near-duplicates.
RPE_FULL = _("Rate of Perceived Exertion")
RIR_FULL = _("Reps In Reserve")
ONE_RM_FULL = _("One-Rep Max")
PR_FULL = _("Personal Record")
REP_MAX_FULL = _("Rep Max")
BMI_FULL = _("Body Mass Index")

# `lazy(..., SafeString)`, not plain `str`: a lazy proxy only gains the
# `__html__` escape hatch (so `{{ field.label }}` renders the `<abbr>`
# tag instead of HTML-escaping it) if told its resolved type is itself
# already-safe.
#
#: A form field `label` (or a fragment to compose into one via
#: `lazy_format_html`) that renders its abbreviation wrapped in an HTML
#: `<abbr title="...">` — the full term shows as a native tooltip (and
#: reaches screen readers) without permanently lengthening the visible
#: label, which matters most in tight layouts like the per-set logging
#: row (`templates/workouts/_performed_exercise_card.html`). Lazy so it
#: can be assigned at class-definition time (`Meta.labels`) — the
#: `abbreviation`/`expansion` arguments are themselves typically
#: `gettext_lazy` results, resolved together only once the label is
#: actually rendered, in whatever language is active then.
#:
#: `tabindex="0"` makes the `<abbr>` itself focusable — necessary for
#: `static/css/base.css`'s `abbr[title]:focus::after` tooltip to be
#: reachable at all on a touchscreen: iOS/Android have no gesture that
#: reveals a plain `title` attribute (no hover, and tapping a
#: non-focusable element doesn't focus it), so without this the
#: expansion would only ever be visible to a mouse user hovering it —
#: exactly backwards for a mobile-first app.
abbr_label = lazy(lambda abbreviation, expansion: format_html(
    '<abbr tabindex="0" title="{}">{}</abbr>', expansion, abbreviation
), SafeString)

#: Lazy `django.utils.html.format_html` — for composing an `abbr_label`
#: together with surrounding plain text into one field label (e.g.
#: "Target <abbr>RPE</abbr>") without forcing early evaluation.
lazy_format_html = lazy(format_html, SafeString)
