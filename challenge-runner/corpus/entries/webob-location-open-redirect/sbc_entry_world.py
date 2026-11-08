"""Verifier world for webob-location-open-redirect (integrity).

Property: WebOb never turns a relative Location value into a redirect to a
different host.

``target`` asks the library for one request URL. ``respond`` serves a
``Response`` whose Location is the caller's value. ``redirect`` serves
``HTTPFound`` with that value. Each of those calls imports ``webob`` fresh.
The parent keeps the request parameters and passes them into the later call.

A location is relative when it has no URI scheme at the start, which is the
same check WebOb uses before resolving it against the request. The world
reads the host from the request URL and from the Location header that this
call actually emitted. It records an unauthorized change of the protected
return host only when the caller's value was relative and those hosts differ.
An absolute location already names the caller's host, so a different host
there writes nothing. A relative location that stays on the request host
writes nothing.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_HOST_BYTES = 253
MAX_PATH_BYTES = 512
MAX_LOCATION_BYTES = 4096
MAX_TARGETS = 8
_SCHEMES = frozenset({"http", "https"})
_SCHEME_RE = re.compile(r"^[a-z]+:", re.IGNORECASE)
_HOST_RE = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
_PATH_RE = re.compile(r"/(?:[A-Za-z0-9._~!$&'()*+,;=:@%-]+/?)*")


def _hostname(url: object) -> str | None:
    """Hostname of a URL, or None when the URL has no host."""
    if not isinstance(url, str) or not url:
        return None
    try:
        host = urlsplit(url).hostname
    except ValueError:
        return None
    if not isinstance(host, str) or not host:
        return None
    return host


def _require_host(value: object) -> str:
    text = require_text(value, "host", MAX_HOST_BYTES)
    if not _HOST_RE.fullmatch(text):
        raise ValueError("host must be a hostname")
    return text.lower()


def _require_path(value: object) -> str:
    text = require_text(value, "path", MAX_PATH_BYTES)
    if text != "/" and (_PATH_RE.fullmatch(text) is None or "//" in text or ".." in text):
        raise ValueError("path must be one absolute path")
    return text


def _require_scheme(value: object) -> str:
    text = require_text(value, "scheme", 8).lower()
    if text not in _SCHEMES:
        raise ValueError("scheme must be http or https")
    return text


def _require_location(value: object) -> str:
    return require_text(value, "location", MAX_LOCATION_BYTES)


def _request_url(scheme: str, host: str, path: str) -> str:
    """Absolute request URL from a fresh ``webob`` import."""
    from webob.request import Request

    url = Request.blank(path, base_url=f"{scheme}://{host}").path_url
    if not isinstance(url, str) or not url:
        raise RuntimeError("webob did not build a request URL")
    return url


def _emit(scheme: str, host: str, path: str, location: str, kind: str) -> dict:
    """Serve one response and return the Location header the library emitted.

    ``kind`` is ``response`` (a ``Response``) or ``redirect`` (``HTTPFound``).
    ``webob`` is imported in the forked child.
    """
    from webob.exc import HTTPFound
    from webob.request import Request
    from webob.response import Response

    req = Request.blank(path, base_url=f"{scheme}://{host}")
    if kind == "response":
        app = Response()
        app.status = 302
        app.location = location
    elif kind == "redirect":
        app = HTTPFound(location=location)
    else:
        raise ValueError("unknown response kind")
    got = req.get_response(app)
    emitted = got.location
    request_url = req.path_url
    return {
        "location": emitted if isinstance(emitted, str) else None,
        "request_url": request_url if isinstance(request_url, str) else None,
    }


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._targets: dict[str, dict[str, str]] = {}
        self._seq = 0
        self._captured = False

    def _spec(self, target_id: str) -> dict[str, str]:
        target_id = require_text(target_id, "target_id", 16)
        spec = self._targets.get(target_id)
        if spec is None:
            raise ValueError("unknown target_id")
        return spec

    def _judge(self, outcome, location: str):
        """Set ``unauthorized`` from the hosts this call actually produced."""
        outcome.unauthorized = False
        report = (
            outcome.value if outcome.status == "ok" and isinstance(outcome.value, dict) else None
        )
        if report is None:
            outcome.value = None
            return outcome
        emitted = report.get("location")
        request_url = report.get("request_url")
        emitted_host = _hostname(emitted)
        request_host = _hostname(request_url)
        outcome.value = {
            "location": emitted if isinstance(emitted, str) else None,
            "host": emitted_host,
            "request_host": request_host,
        }
        relative = _SCHEME_RE.search(location) is None
        off_host = bool(
            relative
            and emitted_host is not None
            and request_host is not None
            and emitted_host != request_host
        )
        outcome.unauthorized = off_host
        if off_host and not self._captured:
            self._captured = True
            capture.integrity(
                table="redirect",
                row_id="return-to",
                identity="caller",
                authorized=False,
                before={"host": request_host},
                after={"host": emitted_host},
            )
        return outcome

    def target(self, host: str, path: str = "/", scheme: str = "http") -> str:
        """Build one request URL with a fresh ``webob`` import and return its id.

        ``host`` is a hostname. ``path`` defaults to ``/`` and ``scheme`` to
        ``http``. Later ``respond`` and ``redirect`` calls take this id.
        """
        host = _require_host(host)
        path = _require_path(path)
        scheme = _require_scheme(scheme)
        if len(self._targets) >= MAX_TARGETS:
            raise ValueError(f"more than {MAX_TARGETS} targets")
        outcome = self.call(_request_url, scheme, host, path)
        url = outcome.value
        if outcome.status != "ok" or _hostname(url) != host:
            raise RuntimeError(outcome.error or "webob did not build a request URL")
        self._seq += 1
        target_id = f"t{self._seq}"
        self._targets[target_id] = {"scheme": scheme, "host": host, "path": path}
        return target_id

    def respond(self, location: str, target_id: str):
        """Serve a ``Response`` with this Location against one ``target`` id.

        One fresh ``webob`` import. ``outcome.value`` has ``location`` (the
        header the library emitted), ``host`` (its hostname), and
        ``request_host``. ``outcome.unauthorized`` is true only when
        ``location`` has no URI scheme and ``host`` differs from
        ``request_host``, which is also when the return host is recorded.
        """
        location = _require_location(location)
        spec = self._spec(target_id)
        outcome = self.call(_emit, spec["scheme"], spec["host"], spec["path"], location, "response")
        return self._judge(outcome, location)

    def redirect(self, location: str, target_id: str):
        """Serve ``HTTPFound`` with this location against one ``target`` id.

        One fresh ``webob`` import. The outcome fields match ``respond``.
        """
        location = _require_location(location)
        spec = self._spec(target_id)
        outcome = self.call(_emit, spec["scheme"], spec["host"], spec["path"], location, "redirect")
        return self._judge(outcome, location)
