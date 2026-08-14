"""Single source of truth for the running IronStack version — read from
the plain-text `VERSION` file at the repo root rather than hardcoded
here or derived from git, so the exact same value is trivially
readable by non-Python tooling too (a future backup/restore shell
script can just `cat VERSION`, and a backup archive can stamp it
alongside the data it captures) without needing to import Django or
parse a `.py` file. `COPY . .` in the Dockerfile bakes it into the
image the same way the application code itself is baked in, so bumping
a release means editing one file and rebuilding — no migration, no
settings change.

Read once and cached — the file never changes without a container
restart anyway.
"""

from functools import lru_cache

from django.conf import settings


@lru_cache(maxsize=1)
def get_version():
    try:
        return (settings.BASE_DIR / "VERSION").read_text().strip()
    except FileNotFoundError:
        return "unknown"
