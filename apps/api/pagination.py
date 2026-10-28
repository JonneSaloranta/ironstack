"""Pagination shared by every apps.api list endpoint."""

from rest_framework.pagination import PageNumberPagination


class ApiPagination(PageNumberPagination):
    """DRF's page-number pagination (25 per page, `?page=N`) with an
    optional `?page_size=N`, capped at 100 - so a mobile client can load
    e.g. the whole exercise library in a request or two instead of one
    per 25 rows, each counted against its per-minute rate limit (issue
    #18). A larger value is clamped to the cap rather than refused."""

    page_size_query_param = "page_size"
    max_page_size = 100
