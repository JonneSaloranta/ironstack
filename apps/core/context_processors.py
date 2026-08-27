"""Global template context — see apps.workouts.context_processors for
why this lives outside the request/response cycle of any one view:
the running version (apps.core.version) is meant to be identifiable
from anywhere in the app, not just the profile page footer that's the
only thing rendering it today.
"""

from .models import SeoSettings
from .version import get_version


def app_version(request):
    return {"app_version": get_version()}


def seo(request):
    """Whether search engines may index this instance
    (apps.core.models.SeoSettings, Profile → Administration → Site &
    SEO) — read into every template's context so base.html's own
    <meta name="robots"> tag (the more universally-respected half of
    this control, robots.txt/apps.core.views.robots_txt being the
    other) doesn't need every single view to pass it through by
    hand."""
    return {"seo_indexing_enabled": SeoSettings.load().search_engine_indexing_enabled}
