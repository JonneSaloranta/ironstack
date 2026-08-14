"""Shared ViewSet shapes — factors out the one ownership pattern that
repeats across Exercise/Program/MeasurementType/ActivityType (system-or-
shared rows are readable by everyone, but only a user's own rows are
ever writable), so each concrete viewset in apps.api.views only has to
say *what* its queryset/editable-queryset are, not re-derive the
create/destroy mechanics every time.
"""

from rest_framework import viewsets


class OwnedResourceViewSet(viewsets.ModelViewSet):
    """`visible_queryset()` (system rows + this user's own) backs list/
    retrieve; `editable_queryset()` (this user's own only) backs update/
    destroy — the exact same split each of these models' web views
    already enforce (services.visible_to vs. an owner=request.user
    filter, or services.editable_by for Program specifically), just
    reused here instead of re-derived. Scoping the queryset itself
    (rather than checking ownership after fetching the object) means a
    request against someone else's row 404s the same way the
    equivalent web view already does, not a 403.
    """

    # False only for Program, which has no `active` field and is
    # genuinely hard-deletable (matches its own web view exactly:
    # "Delete this program? This cannot be undone.").
    soft_delete = True

    def get_queryset(self):
        if self.action in ("update", "partial_update", "destroy"):
            return self.editable_queryset()
        return self.visible_queryset()

    def visible_queryset(self):
        raise NotImplementedError

    def editable_queryset(self):
        return self.visible_queryset().filter(owner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)

    def perform_destroy(self, instance):
        if self.soft_delete:
            instance.active = False
            instance.save(update_fields=["active"])
        else:
            instance.delete()
