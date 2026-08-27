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

# CHANGELOG.md's own Keep a Changelog-style `###` headings, each
# tagged with the same emoji `.github/workflows/ci.yml`'s `create-
# release` job uses for the nearest-matching Conventional Commits type
# — reported directly: this file used to read as "one big list", one
# version bleeding into the next with no visual break at all, and a
# reader had no way to tell at a glance which of two very differently
# structured "what changed" views (this hand-curated one, or a GitHub
# Release's own auto-generated per-commit one) they were looking at.
# Matching emoji/heading style here ties the two together without
# actually sharing any code — CHANGELOG.md still has no idea commits
# or Conventional Commits types even exist. A heading not listed here
# (there isn't one today, but a future one before this list is
# updated) gets the same generic "catch-all" mark the release-notes
# generator itself falls back to for an unrecognized commit type.
_HEADING_EMOJI = {
    "Added": "✨",
    "Fixed": "🐛",
    "Changed": "🔄",
    "Removed": "🗑️",
    "Development": "🔧",
}
_DEFAULT_HEADING_EMOJI = "📌"


def _heading_with_emoji(text):
    # "Added — API" still starts with "Added" — matches on that
    # leading word so a suffixed variant gets the same emoji as its
    # plain counterpart instead of falling through to the default.
    for heading, emoji in _HEADING_EMOJI.items():
        if text == heading or text.startswith(f"{heading} "):
            return f"{emoji} {text}"
    return f"{_DEFAULT_HEADING_EMOJI} {text}"


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
    # Each `##` heading is a version (`[Unreleased]` included) — its
    # own collapsed <details>/<summary>, not blocks flowing straight
    # into the next version's with nothing marking the boundary. Only
    # the first *non-empty* one starts open, so landing on this modal
    # shows the current version's own changes immediately without
    # having to expand anything, while an empty `[Unreleased]` (the
    # normal state right after a release cut, CLAUDE.md's own release
    # workflow) doesn't render as an empty, pointlessly-open section
    # ahead of it.
    sections = []
    # `current_heading` stays `None` for any content appearing before
    # the first `##` line (or the whole document, if it has none at
    # all) — rendered as loose blocks with no <details> wrapper around
    # them at all, the same shape this function always had before
    # per-version sections existed. CHANGELOG.md itself never actually
    # has content there (every real version starts with its own `##`
    # immediately), but nothing here assumes that.
    current_heading = None
    current_blocks = []
    list_items = None
    paragraph_lines = None

    def flush_list():
        nonlocal list_items
        if list_items is not None:
            items = "".join(f"<li>{_inline(item)}</li>" for item in list_items)
            current_blocks.append(f"<ul>{items}</ul>")
            list_items = None

    def flush_paragraph():
        nonlocal paragraph_lines
        if paragraph_lines:
            current_blocks.append(f"<p>{_inline(' '.join(paragraph_lines))}</p>")
            paragraph_lines = None

    def start_section(heading):
        nonlocal current_heading, current_blocks
        flush_paragraph()
        flush_list()
        sections.append((current_heading, current_blocks))
        current_heading = heading
        current_blocks = []

    for raw_line in markdown_text.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_list()
            continue
        if line.startswith("### "):
            flush_paragraph()
            flush_list()
            current_blocks.append(f"<h4>{_inline(_heading_with_emoji(line[4:]))}</h4>")
        elif line.startswith("## "):
            start_section(_inline(line[3:]))
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
    sections.append((current_heading, current_blocks))

    html_parts = []
    opened_one_already = False
    for heading, blocks in sections:
        if not blocks:
            continue
        if heading is None:
            html_parts.append("".join(blocks))
            continue
        open_attr = " open" if not opened_one_already else ""
        opened_one_already = True
        html_parts.append(
            f"<details{open_attr}><summary>{heading}</summary>{''.join(blocks)}</details>"
        )
    return mark_safe("\n".join(html_parts))


@lru_cache(maxsize=1)
def render_changelog_html():
    try:
        text = (settings.BASE_DIR / "CHANGELOG.md").read_text()
    except FileNotFoundError:
        return ""
    return _render(text)
