"""Hands the current page's tour (if it should run) to templates/base.html,
which renders it as JSON for static/js/tutorial.js."""

from django.middleware.csrf import get_token
from django.urls import reverse
from django.utils.translation import gettext, pgettext

from . import services


def tutorial(request):
    tour = services.tour_to_show(request)
    if tour is None:
        return {}
    return {
        "tutorial_data": {
            "key": tour.key,
            "title": str(tour.title),
            "steps": [
                {"anchor": step.anchor, "title": str(step.title), "body": str(step.body)}
                for step in tour.steps
            ],
            "completeUrl": reverse("tutorials:complete", args=[tour.key]),
            "disableUrl": reverse("tutorials:disable"),
            "csrfToken": get_token(request),
            "labels": {
                # Own context: the shared "Back" msgid is translated as
                # the body part in some languages.
                "next": pgettext("tutorial", "Next"),
                "back": pgettext("tutorial", "Previous"),
                "done": pgettext("tutorial", "Done"),
                "skip": gettext("Skip tour"),
                "disable": gettext("Don't show tutorials"),
                # "%(current)s / %(total)s" is filled in by the script.
                "progress": gettext("Step %(current)s of %(total)s"),
            },
        }
    }
