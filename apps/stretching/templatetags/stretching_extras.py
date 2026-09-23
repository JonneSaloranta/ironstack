from django import template

register = template.Library()


@register.filter
def clock(seconds):
    """Whole seconds as a stopwatch-style "m:ss" (90 → "1:30") — the
    unit hold times and routine lengths are planned in, where rounding
    to whole minutes (core_extras.duration) would hide the detail."""
    if seconds is None:
        return ""
    minutes, secs = divmod(int(seconds), 60)
    return f"{minutes}:{secs:02d}"
