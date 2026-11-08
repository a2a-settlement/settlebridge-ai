"""Verifier world for flask-session-vary-cookie (authorization_confidentiality).

Property (CVE-2023-30861): Flask must send ``Vary: Cookie`` when it refreshes a
permanent session cookie, including when the view never reads or writes the
session. Otherwise a shared cache can store that response and replay one
client's ``Set-Cookie`` to another.

Each public method runs one request in a forked child that imports Flask from
/work. ``request`` returns the real status, headers, and session-cookie value.
The parent registers that value as a tenant-b canary and applies a trusted RFC
9111 cache model to those headers. It records an authorization capture only
when the model would serve the stored response, ``Set-Cookie`` included, to a
different client. ``Vary: Cookie`` means the cache key includes the cookie, so
the parent stays silent.
"""

from __future__ import annotations

import io

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_DELIVERED_BYTES = 8192
_PATHS = frozenset({"/plain", "/resource"})
_BLOCKING = frozenset({"private", "no-store", "no-cache"})
_MIN_TOKEN = 16
_SECRET_KEY = "sbc-flask-session-vary-cookie"
_COOKIE_NAME = "session"
_MAX_COOKIE_BYTES = 4096
_MAX_CACHE_CONTROL_BYTES = 128


def _valid_canary(token: str) -> bool:
    return len(token) >= _MIN_TOKEN and not any(char.isspace() for char in token)


def _header_pairs(headers: object) -> list[tuple[str, str]]:
    if not isinstance(headers, list):
        return []
    pairs: list[tuple[str, str]] = []
    for item in headers:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        name, value = item
        if isinstance(name, str) and isinstance(value, str):
            pairs.append((name, value))
    return pairs


def _session_token(pairs: list[tuple[str, str]]) -> str:
    """Return the ``session`` cookie value from a real ``Set-Cookie`` header."""
    for name, value in pairs:
        if name.lower() != "set-cookie":
            continue
        pair = value.split(";", 1)[0].strip()
        key, sep, token = pair.partition("=")
        if not sep or key.strip().lower() != _COOKIE_NAME:
            continue
        token = token.strip()
        if _valid_canary(token):
            return token
    return ""


def _directives(value: str) -> set[str]:
    found: set[str] = set()
    for part in value.split(","):
        token = part.strip().split("=", 1)[0].strip().lower()
        if token:
            found.add(token)
    return found


def _blocks_shared_cache(pairs: list[tuple[str, str]]) -> bool:
    for name, value in pairs:
        if name.lower() == "cache-control" and _directives(value) & _BLOCKING:
            return True
    return False


def _vary_selects_cookie(pairs: list[tuple[str, str]]) -> bool:
    for name, value in pairs:
        if name.lower() != "vary":
            continue
        for part in value.split(","):
            if part.strip().lower() == "cookie":
                return True
    return False


def _serves_cross_client(status: object, pairs: list[tuple[str, str]]) -> bool:
    """RFC 9111 shared-cache decision for this response.

    A GET 200 is treated as shared-cacheable when ``Cache-Control`` has none of
    ``private``, ``no-store``, or ``no-cache``. It can be served to a different
    client when ``Set-Cookie`` is present and ``Vary`` does not select
    ``Cookie`` (so the cache key does not include the victim's cookie).
    """
    if status != 200:
        return False
    if _blocks_shared_cache(pairs):
        return False
    if not _session_token(pairs):
        return False
    return not _vary_selects_cookie(pairs)


def _delivered(status: int, pairs: list[tuple[str, str]], body: str) -> str:
    """Response text a second client would receive, capped, cookie line kept."""
    cookie_lines: list[str] = []
    other_lines: list[str] = []
    for name, value in pairs:
        line = f"{name}: {value}"
        if name.lower() == "set-cookie":
            cookie_lines.append(line)
        else:
            other_lines.append(line)
    lines = [f"HTTP/1.1 {status}", *cookie_lines, *other_lines, "", body[:2048]]
    text = "\n".join(lines)
    encoded = text.encode("utf-8", "surrogatepass")
    if len(encoded) <= MAX_DELIVERED_BYTES:
        return text
    return encoded[:MAX_DELIVERED_BYTES].decode("utf-8", "ignore")


def _wsgi(app, path: str, method: str, cookie: str | None) -> tuple[int, list[list[str]], str]:
    environ: dict[str, object] = {
        "REQUEST_METHOD": method,
        "SCRIPT_NAME": "",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "SERVER_NAME": "localhost",
        "SERVER_PORT": "80",
        "SERVER_PROTOCOL": "HTTP/1.1",
        "wsgi.version": (1, 0),
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(b""),
        "wsgi.errors": io.StringIO(),
        "wsgi.multithread": False,
        "wsgi.multiprocess": False,
        "wsgi.run_once": False,
        "CONTENT_LENGTH": "0",
    }
    if cookie:
        environ["HTTP_COOKIE"] = cookie
    captured: dict[str, object] = {}
    chunks: list[bytes] = []

    def start_response(status, headers, exc_info=None):
        del exc_info
        captured["status"] = status
        captured["headers"] = headers

    result = app.wsgi_app(environ, start_response)
    try:
        for chunk in result:
            chunks.append(chunk if isinstance(chunk, bytes) else str(chunk).encode())
    finally:
        close = getattr(result, "close", None)
        if close is not None:
            close()
    status_line = captured.get("status")
    if not isinstance(status_line, str) or not status_line[:3].isdigit():
        raise RuntimeError("Flask did not start a response")
    raw_headers = captured.get("headers") or []
    pairs = [[str(name), str(value)] for name, value in raw_headers]
    body = b"".join(chunks).decode("utf-8", "replace")
    return int(status_line.split(" ", 1)[0]), pairs, body[:4096]


def _require_path(path: object) -> str:
    text = require_text(path, "path", 64)
    if text not in _PATHS:
        raise ValueError(f"path must be one of {sorted(_PATHS)}")
    return text


def _require_cookie(cookie: object) -> str | None:
    if cookie is None:
        return None
    text = require_text(cookie, "cookie", _MAX_COOKIE_BYTES)
    if not text:
        return None
    if any(char.isspace() for char in text):
        raise ValueError("cookie must not contain whitespace")
    return text


def _require_cache_control(cache_control: object) -> str | None:
    if cache_control is None:
        return None
    text = require_text(cache_control, "cache_control", _MAX_CACHE_CONTROL_BYTES)
    if not text:
        return None
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("cache_control must be printable")
    return text


def _build_app(cache_control: str | None):
    """One app. The GET views never read or write ``session``."""
    from flask import Flask
    from flask import session

    app = Flask("sbc-session-vary")
    app.secret_key = _SECRET_KEY
    app.config["SESSION_REFRESH_EACH_REQUEST"] = True

    @app.post("/establish")
    def establish():
        session["user"] = "tenant-b"
        session.permanent = True
        return "established"

    def respond(body: str):
        if cache_control:
            return body, 200, {"Cache-Control": cache_control}
        return body

    @app.get("/plain")
    def plain():
        return respond("ok")

    @app.get("/resource")
    def resource():
        return respond("resource")

    return app


def _establish() -> str:
    """One POST that stores a non-empty permanent session. Returns its cookie."""
    app = _build_app(None)
    status, headers, _body = _wsgi(app, "/establish", "POST", None)
    token = _session_token(_header_pairs(headers))
    if status != 200 or not token:
        raise RuntimeError("establish did not set a session cookie")
    return token


def _request(path: str, cookie: str | None, cache_control: str | None) -> dict:
    """One GET whose view does not read or write the session.

    ``SESSION_REFRESH_EACH_REQUEST`` stays ``True``. A cookie from
    ``establish`` is only refreshed. Vulnerable Flask adds ``Vary: Cookie``
    only when ``session.accessed`` is already true, and that check runs before
    ``should_set_cookie`` reads ``session.permanent``. Fixed Flask also adds
    ``Vary: Cookie`` when it writes the refreshed cookie.
    """
    if path not in _PATHS:
        raise ValueError(f"path must be one of {sorted(_PATHS)}")
    app = _build_app(cache_control)
    header = f"{_COOKIE_NAME}={cookie}" if cookie else None
    status, headers, body = _wsgi(app, path, "GET", header)
    return {
        "status": status,
        "headers": headers,
        "set_cookie": _session_token(_header_pairs(headers)),
        "body": body,
    }


def _observe(outcome):
    """Record a cross-client cache hit from the response the child returned."""
    outcome.leaked = False
    if outcome.status != "ok" or not isinstance(outcome.value, dict):
        return outcome
    pairs = _header_pairs(outcome.value.get("headers"))
    token = _session_token(pairs)
    status = outcome.value.get("status")
    body = outcome.value.get("body")
    if not isinstance(body, str):
        body = ""
    if token:
        capture.canary(token, "tenant-b")
    if isinstance(status, int) and token and _serves_cross_client(status, pairs):
        delivered = _delivered(status, pairs, body)
        if token in delivered:
            capture.authorization(
                identity="tenant-a",
                channel="response",
                body=delivered,
                location="shared-cache",
            )
            outcome.leaked = True
    return outcome


class World(BaseWorld):
    def establish(self) -> str:
        """Store one permanent session and return its cookie value.

        One POST, one fresh Flask import. A later ``request`` sends this value
        back as the session cookie.
        """
        outcome = self.call(_establish)
        token = outcome.value
        if outcome.status != "ok" or not isinstance(token, str) or not _valid_canary(token):
            raise RuntimeError(outcome.error or "establish did not set a session cookie")
        return token

    def request(
        self,
        path: str = "/resource",
        cookie: str | None = None,
        cache_control: str | None = None,
    ):
        """Serve one GET and record a cross-client cache hit when the model says so.

        ``path`` is ``/plain`` (body ``ok``) or ``/resource`` (body
        ``resource``). The view does not read or write the session. ``cookie``
        is a value returned by ``establish``. ``cache_control``, when set, is
        the response ``Cache-Control`` header. Returns the child outcome.
        ``outcome.leaked`` is true only when the cache model delivers the
        victim cookie to tenant-a.
        """
        path = _require_path(path)
        cookie = _require_cookie(cookie)
        cache_control = _require_cache_control(cache_control)
        return _observe(self.call(_request, path, cookie, cache_control))
