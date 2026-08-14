"""Translation catalog for seeded *content*, not UI chrome — built-in
measurement type names (apps.measurements.migrations' seed data) are
stored in the database in canonical English, the value matched by
`get_or_create(name=...)` elsewhere, so the stored value itself must
never be translated. This module exists solely so `makemessages`
extracts these exact strings into the `.po` catalog; nothing here is
ever imported or executed for its return value — see
apps.exercises.i18n_content and docs/ARCHITECTURE.md
"Internationalization" for the full pattern (`{% trans measurement_type.name %}`
at render time).
"""

from django.utils.translation import gettext_lazy as _

# Measurement type names (apps.measurements.migrations 0002).
MEASUREMENT_TYPE_NAMES = [
    _("Body weight"),
    _("Body fat %"),
    _("Waist"),
    _("Chest"),
    _("Arm"),
    _("Thigh"),
    _("Hip"),
    _("Neck"),
]
