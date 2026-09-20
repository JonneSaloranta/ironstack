from django.core.paginator import Paginator


def paginate_list(request, items, per_page=25):
    """One page of an already-built list (`?page=N`, clamped to a valid
    page) — for the history views whose chart/stats need the full list but
    whose table shouldn't render every row ever logged."""
    return Paginator(items, per_page).get_page(request.GET.get("page"))
