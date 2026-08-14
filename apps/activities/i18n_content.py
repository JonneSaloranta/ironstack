"""Translation catalog for seeded *content*, not UI chrome — built-in
activity type names (apps.activities.migrations' seed data) are stored
in the database in canonical English, the value matched by
`get_or_create(name=...)` elsewhere, so the stored value itself must
never be translated. This module exists solely so `makemessages`
extracts these exact strings into the `.po` catalog; nothing here is
ever imported or executed for its return value — see
apps.exercises.i18n_content and docs/ARCHITECTURE.md
"Internationalization" for the full pattern (`{% trans activity_type.name %}`
at render time).
"""

from django.utils.translation import gettext_lazy as _

# Activity type names (apps.activities.migrations 0002).
ACTIVITY_TYPE_NAMES = [
    _("Running"),
    _("Cycling"),
    _("Swimming"),
    _("Walking"),
    _("Hiking"),
    _("Rowing"),
    _("Yoga"),
    _("Other"),
]
