"""Write parsed config data back as TOML, for the shapes a cnequity config uses.

No TOML writer is in the dependency set, and a config only holds strings,
numbers, booleans, dates, arrays of those, tables and arrays of tables. Inline
tables come out as ordinary sub-tables, which parse to the same data. Every
caller parses the result back before trusting it.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from datetime import date, datetime, time
from typing import Any

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}
_WRAP_AT = 96

#: ``(dotted path) -> comment or None``, appended to that key's line.
Annotate = Callable[[str], "str | None"]


def key(name: str) -> str:
    return name if _BARE_KEY.match(name) else string(name)


def string(value: str) -> str:
    out = []
    for char in value:
        if char in _ESCAPES:
            out.append(_ESCAPES[char])
        elif ord(char) < 0x20 or ord(char) == 0x7F:
            out.append(f"\\u{ord(char):04x}")
        else:
            out.append(char)
    return '"' + "".join(out) + '"'


def value(item: Any, indent: str = "") -> str:
    if isinstance(item, bool):
        return "true" if item else "false"
    if isinstance(item, int):
        return str(item)
    if isinstance(item, float):
        if math.isnan(item):
            return "nan"
        if math.isinf(item):
            return "inf" if item > 0 else "-inf"
        return repr(item)
    if isinstance(item, str):
        return string(item)
    if isinstance(item, datetime | date | time):
        return item.isoformat()
    if isinstance(item, list):
        parts = [value(element) for element in item]
        flat = "[" + ", ".join(parts) + "]"
        if len(flat) + len(indent) <= _WRAP_AT:
            return flat
        inner = indent + "  "
        return "[\n" + "".join(f"{inner}{part},\n" for part in parts) + indent + "]"
    raise TypeError(f"cannot write {type(item).__name__} as TOML")


def _is_table_array(item: Any) -> bool:
    return isinstance(item, list) and bool(item) and all(isinstance(x, dict) for x in item)


def dumps(data: dict, *, annotate: Annotate | None = None) -> str:
    """*data* as TOML text; *annotate* may add a trailing comment per key."""
    lines: list[str] = []
    _table(lines, data, [], annotate, header=None)
    return "\n".join(lines).strip("\n") + "\n"


def _table(
    lines: list[str], table: dict, path: list[str], annotate: Annotate | None, header: str | None
) -> None:
    scalars = [
        (k, v) for k, v in table.items() if not isinstance(v, dict) and not _is_table_array(v)
    ]
    if header is not None and (scalars or not table):
        if lines:
            lines.append("")
        lines.append(header)
    for name, item in scalars:
        line = f"{key(name)} = {value(item)}"
        note = annotate(".".join([*path, name])) if annotate else None
        lines.append(f"{line}  # {note}" if note else line)
    for name, item in table.items():
        sub = [*path, name]
        dotted = ".".join(key(part) for part in sub)
        if isinstance(item, dict):
            _table(lines, item, sub, annotate, header=f"[{dotted}]")
        elif _is_table_array(item):
            for element in item:
                lines.append("")
                lines.append(f"[[{dotted}]]")
                for child_name, child in element.items():
                    if isinstance(child, dict) or _is_table_array(child):
                        raise TypeError(f"nested tables inside [[{dotted}]] are not supported")
                    lines.append(f"{key(child_name)} = {value(child)}")
