"""Self-service API key management — the human-facing (server-rendered,
session-authenticated) counterpart to apps.api's actual API, which never
accepts session auth (see apps.api.auth.ApiKeyAuthentication's own
docstring). Reachable from the Profile page.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.translation import gettext as _

from . import services
from .forms import CRUD_VERBS, ApiKeyCreateForm
from .models import ApiContext

# The session key a freshly created secret is stashed under for exactly
# one page load (see key_created below) — never persisted anywhere else,
# matching apps.api.crypto's "the raw secret is shown once and never
# stored" guarantee.
_SECRET_SESSION_KEY = "api_key_secret_once"


@login_required
def key_list(request):
    return render(
        request,
        "api/key_list.html",
        {
            "keys": services.api_keys_for(request.user),
            "remaining_quota": services.remaining_key_quota(request.user),
        },
    )


@login_required
def key_create(request):
    if services.remaining_key_quota(request.user) <= 0:
        messages.error(request, _("You've reached your maximum number of API keys."))
        return redirect("api_keys:key-list")

    form = ApiKeyCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        api_key, raw_secret = services.create_api_key(
            request.user, name=form.cleaned_data["name"], permissions=form.permissions()
        )
        request.session[_SECRET_SESSION_KEY] = raw_secret
        return redirect("api_keys:key-created", pk=api_key.pk)

    # Reshaped for the template into one row per context, each holding
    # its 4 BoundFields in CRUD_VERBS order — a template can't easily
    # look up a dynamically-named field (`form["profile__can_create"]`)
    # on its own, so the grid is assembled here instead.
    grid_rows = [
        {
            "context_label": context_label,
            "cells": [form[f"{context_value}__{verb_field}"] for verb_field, _vl in CRUD_VERBS],
        }
        for context_value, context_label in ApiContext.choices
    ]
    return render(
        request,
        "api/key_form.html",
        {
            "form": form,
            "grid_rows": grid_rows,
            "crud_labels": [label for _field, label in CRUD_VERBS],
        },
    )


@login_required
def key_created(request, pk):
    api_key = get_object_or_404(services.api_keys_for(request.user), pk=pk)
    raw_secret = request.session.pop(_SECRET_SESSION_KEY, None)
    if raw_secret is None:
        # Already shown once, or a direct/bookmarked visit to this URL
        # — there's nothing left to reveal a second time, by design.
        return redirect("api_keys:key-list")
    return render(request, "api/key_created.html", {"api_key": api_key, "raw_secret": raw_secret})


@login_required
def key_revoke(request, pk):
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    api_key = get_object_or_404(services.api_keys_for(request.user), pk=pk)
    services.revoke_api_key(api_key)
    messages.success(request, _("API key revoked."))
    return redirect("api_keys:key-list")
