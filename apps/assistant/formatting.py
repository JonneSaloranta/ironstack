"""Renders a model's reply as HTML — a deliberately tiny Markdown subset.

The system prompt asks for plain text with **bold**, `code`, `- `
bullets and numbered lists (apps.assistant.prompts), so that's all this
understands; `#` headings come out as bold paragraphs. Everything is
HTML-escaped first and only these few tags are re-introduced, and there
are no links or images at all: a reply is model output, and model output
can echo whatever text ended up in its context, so it never gets to put
a clickable URL or markup on the page. Same "small, purpose-built
parser instead of a Markdown dependency" reasoning as
apps.core.changelog, but written for untrusted input.
"""

import re
from html import escape

from django.utils.safestring import mark_safe

_BULLET = re.compile(r"^\s*[-*•]\s+(.*)$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEADING = re.compile(r"^\s*#{1,6}\s+(.*)$")


def _inline(text):
    text = escape(text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", text)
    return text


def render_reply(text):
    blocks = []
    list_tag = None
    list_items = []
    paragraph = []

    def flush_paragraph():
        if paragraph:
            blocks.append(f"<p>{'<br>'.join(_inline(line) for line in paragraph)}</p>")
            paragraph.clear()

    def flush_list():
        nonlocal list_tag
        if list_tag:
            items = "".join(f"<li>{_inline(item)}</li>" for item in list_items)
            blocks.append(f"<{list_tag}>{items}</{list_tag}>")
            list_items.clear()
            list_tag = None

    for line in (text or "").splitlines():
        if not line.strip():
            flush_paragraph()
            flush_list()
            continue
        bullet = _BULLET.match(line)
        numbered = None if bullet else _NUMBERED.match(line)
        if bullet or numbered:
            flush_paragraph()
            tag = "ul" if bullet else "ol"
            if list_tag != tag:
                flush_list()
                list_tag = tag
            list_items.append((bullet or numbered).group(1))
            continue
        heading = _HEADING.match(line)
        if heading:
            flush_paragraph()
            flush_list()
            blocks.append(f"<p><strong>{_inline(heading.group(1))}</strong></p>")
            continue
        flush_list()
        paragraph.append(line.strip())
    flush_paragraph()
    flush_list()
    return mark_safe("".join(blocks))
