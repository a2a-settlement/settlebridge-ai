"""Verifier world for jinja-xmlattr-keys (injection_execution).

The property: the ``xmlattr`` filter never emits a key that starts another
attribute or ends the tag, whatever characters the key contains.

Parser-breakout convention: ``render`` runs the template in a forked child and
returns the HTML string. The parent parses that string with the stdlib
``HTMLParser`` and records an injection sentinel only when a key provably broke
out of the single ``<p>`` — an attribute whose name is not exactly a supplied
key, a second tag, or the CANARY text outside ``<p>...</p>``. The test's
assertions are not the verdict. Values are escaped, so a malicious value must
not fire. Jinja 3.1.3 rejects spaces in keys only; ``/`` and ``=`` still break
out. Jinja 3.1.4 rejects those characters with ``ValueError`` before any HTML
is produced.
"""

from __future__ import annotations

from html.parser import HTMLParser

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_KEYS = 32
MAX_KEY_BYTES = 512
MAX_VALUE_BYTES = 8 * 1024
MAX_HTML_BYTES = 2 * 1024 * 1024
_TEMPLATE = "<p{{ d|xmlattr }}>CANARY</p>"
_CANARY = "CANARY"


def _require_mapping(mapping: object) -> dict[str, str]:
    if type(mapping) is not dict:
        raise TypeError("mapping must be a plain dict")
    if len(mapping) > MAX_KEYS:
        raise ValueError(f"mapping has more than {MAX_KEYS} keys")
    checked: dict[str, str] = {}
    for index, (key, value) in enumerate(mapping.items()):
        checked[require_text(key, f"mapping key {index}", MAX_KEY_BYTES)] = require_text(
            value, f"mapping value {index}", MAX_VALUE_BYTES
        )
    return checked


def _render(mapping: dict) -> str:
    import jinja2

    rendered = jinja2.Template(_TEMPLATE, autoescape=True).render(d=mapping)
    if not isinstance(rendered, str):
        raise TypeError("template render did not return a string")
    # Markup is a str subclass; the child report has to be a plain str.
    return str.__str__(rendered)


class _Fragment(HTMLParser):
    """Collect start tags, end tags, and text from one rendered fragment."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[tuple] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.events.append(("start", tag, tuple(attrs)))

    def handle_endtag(self, tag: str) -> None:
        self.events.append(("end", tag, ()))

    def handle_data(self, data: str) -> None:
        if data:
            self.events.append(("data", data, ()))


def _breakout_location(html: str, keys: frozenset[str]) -> str | None:
    """Return a capture location when ``html`` is not one safe ``<p>`` element."""
    fragment = _Fragment()
    try:
        fragment.feed(html)
        fragment.close()
    except Exception as exc:
        return f"jinja2.xmlattr: HTML parser failed ({type(exc).__name__})"

    starts = [event for event in fragment.events if event[0] == "start"]
    ends = [event for event in fragment.events if event[0] == "end"]
    if len(starts) != 1 or starts[0][1] != "p" or len(ends) != 1 or ends[0][1] != "p":
        return "jinja2.xmlattr: second tag or missing <p>"

    unexpected = [name for name, _value in starts[0][2] if name not in keys]
    if unexpected:
        shown = ", ".join(repr(name) for name in unexpected)
        return f"jinja2.xmlattr: attributes not supplied keys: {shown}"

    depth = 0
    inside: list[str] = []
    outside: list[str] = []
    for kind, payload, _rest in fragment.events:
        if kind == "start":
            depth += 1
        elif kind == "end":
            depth -= 1
        elif kind == "data":
            (inside if depth > 0 else outside).append(payload)
    if _CANARY in "".join(outside) or _CANARY not in "".join(inside):
        return "jinja2.xmlattr: CANARY outside <p>"
    return None


class World(BaseWorld):
    def render(self, mapping: dict):
        """Render ``mapping`` through ``xmlattr``.

        Returns the child outcome. ``outcome.broke_out`` is true only when the
        parent's HTML parse shows that a key left the single ``<p>`` tag.
        """
        checked = _require_mapping(mapping)
        outcome = self.call(_render, checked)
        outcome.broke_out = False
        rendered = outcome.value
        if outcome.status != "ok" or type(rendered) is not str:
            return outcome
        if len(rendered.encode("utf-8", "surrogatepass")) > MAX_HTML_BYTES:
            return outcome
        location = _breakout_location(rendered, frozenset(checked))
        if location is None:
            return outcome
        outcome.broke_out = True
        capture.injection("command", location=location)
        return outcome
