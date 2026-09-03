"""Global template context — see apps.workouts.context_processors for
why this lives outside the request/response cycle of any one view:
the running version (apps.core.version) is meant to be identifiable
from anywhere in the app, not just the profile page footer that's the
only thing rendering it today.
"""

from django.conf import settings

from .models import SeoSettings
from .version import get_version


def app_version(request):
    return {"app_version": get_version()}


def admin_contact(request):
    """settings.ADMIN_CONTACT_EMAIL, read into every template's
    context the same way push()/seo() below already are — see that
    setting's own comment (config/settings/base.py) for what it's
    for. Empty string, not missing, when unset — templates can test
    it directly with {% if admin_contact_email %} either way."""
    return {"admin_contact_email": settings.ADMIN_CONTACT_EMAIL}


def push(request):
    """Whether Web Push is configured on this instance at all
    (settings.PUSH_ENABLED, docs/SECURITY.md "Web Push notifications")
    and, if so, the VAPID public key the client needs for
    `pushManager.subscribe({applicationServerKey})` — read into every
    template's context so the profile page's "Notifications" card can
    render (or not) without every view that might reach it passing
    this through by hand, the same reasoning `seo` above already
    follows for its own instance-wide setting."""
    return {
        "push_enabled": settings.PUSH_ENABLED,
        "push_vapid_public_key": settings.VAPID_PUBLIC_KEY,
    }


def seo(request):
    """Whether search engines may index this instance
    (apps.core.models.SeoSettings, Profile → Administration → Site &
    SEO) — read into every template's context so base.html's own
    <meta name="robots"> tag (the more universally-respected half of
    this control, robots.txt/apps.core.views.robots_txt being the
    other) doesn't need every single view to pass it through by
    hand."""
    return {"seo_indexing_enabled": SeoSettings.load().search_engine_indexing_enabled}
