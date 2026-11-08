"""Verifier world for tornado-cookie-attribute-injection (injection_execution).

The property: ``RequestHandler.set_cookie`` never lets a caller-supplied value
add or change cookie attributes.

``set_cookie`` runs in a forked child that imports tornado fresh from /work and
returns the ``Set-Cookie`` header plus the attribute map the library stored on
its morsel. The parent parses that header with a trusted split on ``;`` and
records an injection sentinel only when the parsed attributes are not exactly
the stored ones: a repeated attribute, an extra attribute, a stored value that
is not the parsed value, or a control character in the header. Those are the
positions where caller text has left its slot and landed on another attribute,
including ``Path``. Octal-escaped cookie values, and a quoted ``Comment`` whose
unquoted text still matches the stored comment, stay in their slot and do not
fire.
"""

from __future__ import annotations

import re

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_NAME_BYTES = 256
MAX_VALUE_BYTES = 4096
MAX_ATTRIBUTES = 16
MAX_ATTRIBUTE_NAME_BYTES = 64
MAX_ATTRIBUTE_VALUE_BYTES = 1024
MAX_HEADER_BYTES = 16 * 1024
_MAX_INT = 10**12
_FLAGS = frozenset({"secure", "httponly"})
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_QUOTED_ESCAPE = re.compile(r"\\(?:([0-3][0-7][0-7])|(.))")


def _require_attribute_value(value: object, name: str) -> object:
    if type(value) is bool:
        return value
    if type(value) is int:
        if not -_MAX_INT <= value <= _MAX_INT:
            raise ValueError(f"attribute {name} is out of range")
        return value
    return require_text(value, f"attribute {name}", MAX_ATTRIBUTE_VALUE_BYTES)


def _require_attributes(attributes: object) -> dict[str, object]:
    if attributes is None:
        return {}
    if type(attributes) is not dict:
        raise TypeError("attributes must be a plain dict")
    if len(attributes) > MAX_ATTRIBUTES:
        raise ValueError(f"attributes has more than {MAX_ATTRIBUTES} keys")
    checked: dict[str, object] = {}
    for index, (key, value) in enumerate(attributes.items()):
        name = require_text(key, f"attribute name {index}", MAX_ATTRIBUTE_NAME_BYTES)
        if name in checked:
            raise ValueError(f"duplicate attribute {name}")
        checked[name] = _require_attribute_value(value, name)
    return checked


def _stored(morsel: object) -> dict[str, str | bool]:
    stored: dict[str, str | bool] = {}
    for key, val in morsel.items():
        if type(key) is not str:
            continue
        if val is False or val is None or val == "":
            continue
        if key in _FLAGS:
            stored[key] = True
        elif type(val) is bool:
            stored[key] = val
        else:
            stored[key] = str(val)
    return stored


def _emit(name: str, value: str, attributes: dict) -> dict:
    import warnings

    import tornado.httputil
    import tornado.web

    class _Connection:
        def set_close_callback(self, callback) -> None:
            return None

    application = tornado.web.Application([])
    request = tornado.httputil.HTTPServerRequest(
        method="GET",
        uri="/",
        connection=_Connection(),
    )
    handler = tornado.web.RequestHandler(application, request)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        handler.set_cookie(name, value, **attributes)
    morsels = list(handler._new_cookie.values())
    header = "\n".join(morsel.OutputString(None) for morsel in morsels)
    stored = _stored(morsels[0]) if len(morsels) == 1 else {}
    return {"header": header, "stored": stored}


def _unquote(value: str) -> str:
    if len(value) < 2 or value[0] != '"' or value[-1] != '"':
        return value

    def replace(match: re.Match) -> str:
        if match.group(1):
            return chr(int(match.group(1), 8))
        return match.group(2)

    return _QUOTED_ESCAPE.sub(replace, value[1:-1])


def _same_value(key: str, stored: str, parsed: str) -> bool:
    if parsed == stored:
        return True
    if key == "comment":
        return _unquote(parsed) == stored
    return False


def _attributes(header: str) -> list[tuple[str, str | None]]:
    _pair, sep, rest = header.partition(";")
    if not sep:
        return []
    parsed: list[tuple[str, str | None]] = []
    for piece in rest.split(";"):
        item = piece.strip()
        if not item:
            continue
        name, eq, value = item.partition("=")
        if eq:
            parsed.append((name.strip().lower(), value))
        else:
            parsed.append((name.strip().lower(), None))
    return parsed


def _injection_location(header: str, stored: dict) -> str | None:
    if _CONTROL.search(header):
        return "tornado.set_cookie: control character in Set-Cookie"
    parsed = _attributes(header)
    names = [name for name, _value in parsed]
    if len(names) != len(set(names)):
        return "tornado.set_cookie: repeated cookie attribute"
    found = dict(parsed)
    expected: set[str] = set()
    for key, val in stored.items():
        if type(key) is not str:
            return "tornado.set_cookie: stored attribute name is not text"
        name = key.lower()
        if name in _FLAGS:
            if val:
                expected.add(name)
                if name not in found or found[name] is not None:
                    return f"tornado.set_cookie: flag {name} changed"
            elif name in found:
                return f"tornado.set_cookie: flag {name} added"
            continue
        expected.add(name)
        if name not in found or found[name] is None or type(val) is not str:
            return f"tornado.set_cookie: attribute {name} missing"
        if not _same_value(name, val, found[name]):
            return f"tornado.set_cookie: attribute {name} changed"
    extra = sorted(set(found) - expected)
    if extra:
        return "tornado.set_cookie: extra attributes: " + ", ".join(extra)
    return None


class World(BaseWorld):
    def set_cookie(self, name: str, value: str, attributes: dict | None = None):
        """Set one cookie and return the ``Set-Cookie`` header.

        ``attributes`` is passed through as keyword arguments (mixed-case
        names included). Returns the child outcome. On success ``outcome.value``
        is ``{"header", "stored"}``. ``outcome.injected`` is true only when a
        trusted parse of the header shows that caller text added, repeated, or
        changed a cookie attribute.
        """
        checked_name = require_text(name, "name", MAX_NAME_BYTES)
        checked_value = require_text(value, "value", MAX_VALUE_BYTES)
        checked_attributes = _require_attributes(attributes)
        outcome = self.call(_emit, checked_name, checked_value, checked_attributes)
        outcome.injected = False
        if outcome.status != "ok" or type(outcome.value) is not dict:
            return outcome
        header = outcome.value.get("header")
        stored = outcome.value.get("stored")
        if type(header) is not str or type(stored) is not dict:
            return outcome
        if len(header.encode("utf-8", "surrogatepass")) > MAX_HEADER_BYTES:
            return outcome
        location = _injection_location(header, stored)
        if location is None:
            return outcome
        outcome.injected = True
        capture.injection("command", location)
        return outcome
