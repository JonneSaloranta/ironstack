"""Shared "export this, import it somewhere else" envelope — asked for
directly, for `apps.programs.Program` and `apps.nutrition.Recipe`: let
a user download one of their own as a plain `.json` file and hand it
to someone else (a different account, a different self-hosted
IronStack instance entirely) to import back in.

Deliberately a hand-rolled dict format, not the REST API's own
`ModelSerializer`s (`apps.api.serializers`) — those reference related
rows by primary key, which means nothing once the file crosses into a
different database. Everything here is instead keyed by *natural*
identifiers: a system exercise/meal slot by its own name (globally
unique among system rows — see `Exercise`/`MealSlot`'s own
`unique_system_*_name` constraints), a food imported from
OpenFoodFacts by its barcode — the only identifiers guaranteed to mean
the same thing on the machine that exported a file and the one
importing it.

`SCHEMA_VERSION` is this wire format's own version, independent of
`apps.core.version.get_version()` (the *app's* version, included in
every export purely as an at-a-glance "exported by this build" — never
compared against automatically, since an app version number alone
can't reliably say whether this module's own dict shape changed
between two releases). Bump it only if a future change here would
make an older export unreadable outright; older files stay readable
by a newer instance for as long as this module's own `parse_envelope`
still recognizes their `schema_version`.

**Keeping old exports importable forever, without pre-building
version-migration machinery nothing needs yet:**

- A field that only ever gets *added* — never needs a bump.
  `import_program`/`import_recipe` (`apps.programs`/`apps.nutrition`
  `services.py`) read every field with `payload.get(key, default)`,
  so a file exported before that field existed simply lacks the key
  and gets the same default a brand new payload would. This is the
  expected, common way this format grows.
- Renaming a key, changing what an existing key means, or restructuring
  how something nests is the one case that actually needs a bump: `1`
  → `2`, plus a small upgrade step added to the *top* of the relevant
  `import_*` function that normalizes an old `schema_version`'s shape
  into the current one before the rest of the function ever runs — so
  an old file keeps importing exactly as before, just translated
  first, forever. Nothing like that exists yet because there is no
  `schema_version` 2 to migrate *from* — writing that shim now, for a
  hypothetical future shape, would just be speculative code with
  nothing real to test it against.
"""

import json

from django.utils import timezone
from django.utils.translation import gettext as _

from . import version as version_info

SCHEMA_VERSION = 1


class ImportValidationError(Exception):
    """Raised for anything wrong with an import file itself — not
    valid JSON, a missing/malformed envelope, the wrong `kind`, a
    `schema_version` newer than this build understands, or a required
    field missing from the payload. Always caught by the importing
    view and shown as a plain form error, never left to become a
    500 — a user-supplied file is exactly the kind of untrusted input
    that must never be able to take the request down with it."""


def build_envelope(kind, payload):
    """Wraps `payload` (a plain dict — `apps.programs.services.
    export_program`/`apps.nutrition.services.export_recipe`) in the
    shared envelope, ready for `json.dumps`."""
    return {
        "ironstack_export": {
            "schema_version": SCHEMA_VERSION,
            "kind": kind,
            "app_version": version_info.get_version(),
            "exported_at": timezone.now().isoformat(),
        },
        kind: payload,
    }


def parse_envelope(raw, *, expected_kind):
    """Parses and validates an uploaded export file's raw bytes/text,
    returning `(payload, app_version)` for the caller to hand to its
    own `import_*` function. Raises `ImportValidationError` for
    anything not shaped like a real export of `expected_kind` — this
    function only ever checks the envelope itself, never the
    kind-specific payload inside it (that's each `import_*`
    function's own job, so a program import doesn't need to know what
    a recipe payload should look like or vice versa).

    An `app_version` far ahead of this instance's own isn't rejected
    outright — the envelope shape (and therefore whether this can be
    read at all) rarely changes even when features do, so a stale
    instance can usually still import a file from a newer one just
    fine. It's returned so the caller can choose to show it as an
    informational "this was exported by a newer version" note rather
    than silently saying nothing about it.
    """
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError) as exc:
        raise ImportValidationError(_("That file isn't valid JSON.")) from exc
    if not isinstance(data, dict):
        raise ImportValidationError(_("That file doesn't look like an IronStack export."))
    envelope = data.get("ironstack_export")
    if not isinstance(envelope, dict):
        raise ImportValidationError(_("That file doesn't look like an IronStack export."))
    schema_version = envelope.get("schema_version")
    if not isinstance(schema_version, int) or schema_version > SCHEMA_VERSION:
        raise ImportValidationError(
            _(
                "This file was exported by a newer version of IronStack that "
                "this instance doesn't know how to read yet."
            )
        )
    kind = envelope.get("kind")
    if kind != expected_kind:
        raise ImportValidationError(
            _("That file is a “%(kind)s” export, not a “%(expected)s”.")
            % {"kind": kind or _("unknown"), "expected": expected_kind}
        )
    payload = data.get(expected_kind)
    if not isinstance(payload, dict):
        raise ImportValidationError(_("That file doesn't look like an IronStack export."))
    return payload, envelope.get("app_version", "unknown")
