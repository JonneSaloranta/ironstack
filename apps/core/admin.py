
from django.contrib import admin
from django.utils.translation import gettext_lazy as _

# Restyles Django's own admin (templates/admin/base_site.html,
# static/css/admin_theme.css) to match IronStack's branding/palette
# instead of building a parallel custom admin page — see
# docs/ARCHITECTURE.md "API layer" for the same "don't duplicate an
# abstraction Django already provides" reasoning applied here. Site-wide,
# so it lives in apps.core rather than any single feature app.
admin.site.site_header = "IronStack"
admin.site.site_title = "IronStack"
admin.site.index_title = _("Administration")
