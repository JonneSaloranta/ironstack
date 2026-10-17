"""A minimal TOON (Token-Oriented Object Notation) encoder for the
assistant's tool results. See docs/ASSISTANT.md "Tools".

TOON carries the same data model as JSON in fewer tokens: objects are
`key: value` lines indented by two spaces per level, and an array of
objects that all have the same primitive fields becomes one header plus
one comma-separated row each - exactly the shape most tool results have
(foods, sets, readings):

    foods[2]{id,name,calories}:
      12,Oats,380
      15,"Butter, salted",717

Tools keep their rows flat for this (tools._joined turns a short list
into one "a; b" string), since a row holding a list or an object can't
be a table row. Only encoding is needed - the model reads TOON, nothing here parses it -
so this follows the spec's encoder rules (https://toonformat.dev)
instead of pulling in a dependency: comma delimiter, two-space
indentation, `[N]` length markers, and strings quoted only where a bare
value would be ambiguous.

Input is plain JSON data (dict/list/str/int/float/bool/None); tools.run
round-trips a tool's result through DjangoJSONEncoder first, so dates,
Decimals and lazy strings are already strings or numbers by then.
"""

import math
import re
from decimal import Decimal

INDENT = "  "
DELIMITER = ","

_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]*$")
_NUMERIC_LIKE = re.compile(r"^-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?$|^0\d+$")
_NEEDS_QUOTES = re.compile(r'[:"\\\[\]{}\n\r\t,]')
_ESCAPES = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t"}


def encode(value):
    """The TOON text for `value` - normally a tool's result object."""
    if isinstance(value, dict):
        return "\n".join(_object_lines(value, 0))
    if isinstance(value, list):
        return "\n".join(_array_lines(None, value, 0))
    return _primitive(value)


def _is_primitive(value):
    return value is None or isinstance(value, (str, int, float, bool))


def _primitive(value):
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, (int, float)):
        return _number(value)
    return _string(value)


def _number(value):
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return "null"
        if value.is_integer():
            value = int(value)
        else:
            # Canonical decimal form - never exponent notation.
            return format(Decimal(repr(value)), "f")
    return "0" if value == 0 else str(value)


def _quoted(value):
    return '"' + "".join(_ESCAPES.get(char, char) for char in value) + '"'


def _string(value):
    if (
        value == ""
        or value != value.strip()
        or value in ("true", "false", "null")
        or value.startswith("-")
        or _NUMERIC_LIKE.match(value)
        or _NEEDS_QUOTES.search(value)
    ):
        return _quoted(value)
    return value


def _key(key):
    # Stricter than a value: anything but an identifier-like key is quoted.
    key = str(key)
    return key if _BARE_KEY.match(key) else _quoted(key)


def _object_lines(obj, depth):
    lines = []
    pad = INDENT * depth
    for key, value in obj.items():
        if isinstance(value, list):
            lines.extend(_array_lines(_key(key), value, depth))
        elif isinstance(value, dict):
            lines.append(f"{pad}{_key(key)}:")
            lines.extend(_object_lines(value, depth + 1))
        else:
            lines.append(f"{pad}{_key(key)}: {_primitive(value)}")
    return lines


def _tabular_fields(items):
    """The field list if `items` can be a table: every item an object
    whose values are all primitives. Else None.

    One deliberate step past the spec's "same keys in every row": rows
    with different keys still make one table over all their fields, and
    a missing field is written as null. Tool results leave optional
    fields out (a food without allergens), and to the model an absent
    field and a null one mean the same - while the table is what saves
    the tokens. A spec decoder reads it back with explicit nulls."""
    if not items or not all(isinstance(item, dict) and item for item in items):
        return None
    fields = {}
    for item in items:
        if not all(_is_primitive(v) for v in item.values()):
            return None
        fields.update(dict.fromkeys(item))
    return list(fields)


def _array_lines(key, items, depth):
    pad = INDENT * depth
    prefix = f"{pad}{key or ''}[{len(items)}]"
    if not items:
        return [f"{prefix}:"]
    if all(_is_primitive(item) for item in items):
        return [f"{prefix}: " + DELIMITER.join(_primitive(item) for item in items)]
    fields = _tabular_fields(items)
    if fields is not None:
        row_pad = INDENT * (depth + 1)
        return [f"{prefix}{{{DELIMITER.join(_key(f) for f in fields)}}}:"] + [
            row_pad + DELIMITER.join(_primitive(item.get(f)) for f in fields) for item in items
        ]
    lines = [f"{prefix}:"]
    for item in items:
        lines.extend(_list_item_lines(item, depth + 1))
    return lines


def _list_item_lines(item, depth):
    """One `- ` entry of an expanded list. An object's first field sits
    on the hyphen line and the rest line up under it - rendering the
    object one level deeper and swapping the first line's indentation
    for the hyphen keeps every nested level where the spec puts it."""
    pad = INDENT * depth
    if isinstance(item, dict):
        if not item:
            return [f"{pad}-"]
        lines = _object_lines(item, depth + 1)
        lines[0] = f"{pad}- {lines[0].lstrip()}"
        return lines
    if isinstance(item, list):
        # The `[N]:` header takes the hyphen's own level; its items go one
        # level below that, not below the hyphen's content.
        lines = _array_lines(None, item, depth)
        lines[0] = f"{pad}- {lines[0].lstrip()}"
        return lines
    return [f"{pad}- {_primitive(item)}"]
