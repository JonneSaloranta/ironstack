"""Global template context — same reasoning as
apps.workouts.context_processors.active_workout_session: the one-time
onboarding prompt (templates/accounts/_onboarding_modal.html) has to be
able to appear on whatever page a user happens to land on right after
login, not just one specific view, so gating it per-view would mean
threading this flag through every single view in the app instead of
once, here.
"""

from .forms import OnboardingForm


def onboarding(request):
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated or user.onboarding_completed:
        return {}
    # Deliberately not named "form": this context is merged into every
    # page's template context (see this module's own docstring), and
    # several pages already have their own view-specific "form" (e.g.
    # ProfileView's own preferences form on the very page this modal
    # appears over) — a shared key name would risk one silently
    # shadowing the other depending on context-processor/view merge
    # order.
    return {"show_onboarding": True, "onboarding_form": OnboardingForm(user=user)}
