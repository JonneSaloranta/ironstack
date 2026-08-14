"""Renders `CHANGELOG.md` (repo root) to HTML for the profile page's
version-number modal (`templates/accounts/profile.html`) — the version
number, the previous versions, and what changed, all in one place a
user can actually read without leaving the app.

Deliberately not a real Markdown library: `CHANGELOG.md` only ever
uses a handful of constructs (`#`/`##`/`###` headings, `- ` bullets
with soft-wrapped continuation lines, `` `code` ``/`**bold**`/
`[text](url)` inline spans) — this file is the *only* thing that
writes CHANGELOG.md, so a parser narrowly scoped to exactly what it
actually contains is simpler and has no new dependency to track,
matching CLAUDE.md's "avoid unnecessary dependencies". If the file's
formatting ever needs more than this, that's the signal to reach for
a real Markdown library instead of growing this by hand.
"""

import re
from functools import lru_cache
from html import escape

from django.conf import settings
from django.utils.safestring import mark_safe


def _inline(text):
    """Escapes first (CHANGELOG.md is repo content, not user input, but
    escaping before re-introducing a few specific HTML tags is the safe
    order regardless), then applies the small set of inline spans this
    file actually uses."""
    text = escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2" target="_blank" rel="noopener">\1</a>', text
    )
    return text


def _render(markdown_text):
    blocks = []
    list_items = None
    paragraph_lines = None

    def flush_list():
        nonlocal list_items
        if list_items is not None:
            items = "".join(f"<li>{_inline(item)}</li>" for item in list_items)
            blocks.append(f"<ul>{items}</ul>")
            list_items = None

    def flush_paragraph():
        nonlocal paragraph_lines
        if paragraph_lines:
            blocks.append(f"<p>{_inline(' '.join(paragraph_lines))}</p>")
            paragraph_lines = None

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue
        if line.startswith("### "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<h4>{_inline(line[4:])}</h4>")
        elif line.startswith("## "):
            flush_paragraph()
            flush_list()
            blocks.append(f"<h3>{_inline(line[3:])}</h3>")
        elif line.startswith("# "):
            # The document's own top-level title — skipped, since
            # whatever includes this HTML supplies its own heading.
            flush_paragraph()
            flush_list()
        elif line.startswith("- "):
            flush_paragraph()
            if list_items is None:
                list_items = []
            list_items.append(line[2:])
        elif list_items is not None:
            # A soft-wrapped continuation of the current bullet, not a
            # new paragraph — CHANGELOG.md wraps long bullets onto
            # multiple source lines the way the rest of this repo's
            # prose does.
            list_items[-1] += " " + line
        else:
            paragraph_lines = (paragraph_lines or []) + [line]

    flush_paragraph()
    flush_list()
    return mark_safe("\n".join(blocks))


@lru_cache(maxsize=1)
def render_changelog_html():
    try:
        text = (settings.BASE_DIR / "CHANGELOG.md").read_text()
    except FileNotFoundError:
        return ""
    return _render(text)
