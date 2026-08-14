"""Global template context — see apps.workouts.context_processors for
why this lives outside the request/response cycle of any one view:
the running version (apps.core.version) is meant to be identifiable
from anywhere in the app, not just the profile page footer that's the
only thing rendering it today.
"""

from .version import get_version


def app_version(request):
    return {"app_version": get_version()}
