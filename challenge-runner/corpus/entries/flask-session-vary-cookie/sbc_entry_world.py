"""Verifier world for flask-session-vary-cookie (authorization_confidentiality).

Property (CVE-2023-30861): Flask must send ``Vary: Cookie`` when it refreshes a
permanent session cookie, including when the view never reads or writes the
session. Otherwise a shared cache can store that response and replay one
client's ``Set-Cookie`` to another.

The forked child imports Flask from /work, runs the request flow, and returns
the real status, headers, and session-cookie value. The parent registers that
value as a tenant-b canary and applies a trusted RFC 9111 cache model to those
headers. It records an authorization capture only when the model would serve
the stored response, ``Set-Cookie`` included, to a different client. ``Vary:
Cookie`` means the cache key includes the cookie, so the parent stays silent.
"""

from __future__ import annotations

import io

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld

MAX_DELIVERED_BYTES = 8192
_MODES = frozenset({"plain", "refresh", "private"})
_BLOCKING = frozenset({"private", "no-store", "no-cache"})
_MIN_TOKEN = 16
_SECRET_KEY = "sbc-flask-session-vary-cookie"
_COOKIE_NAME = "session"


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


def _exchange(mode: str) -> dict:
    """Run the victim flow inside the forked child, against Flask from /work.

    ``SESSION_REFRESH_EACH_REQUEST`` stays at its default ``True``. ``POST
    /establish`` stores a non-empty permanent session. The measured ``GET
    /resource`` sends that cookie back and does not touch ``session``, so the
    cookie is only refreshed. Vulnerable Flask adds ``Vary: Cookie`` only when
    ``session.accessed`` is already true, and that check runs before
    ``should_set_cookie`` reads ``session.permanent``. Fixed Flask also adds
    ``Vary: Cookie`` when it writes the refreshed cookie.
    """
    if mode not in _MODES:
        raise ValueError(f"mode must be one of {sorted(_MODES)}")
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

    @app.get("/plain")
    def plain():
        return "ok"

    @app.get("/resource")
    def resource():
        # Do not read or write the session. ``private`` only marks the response
        # unusable by a shared cache; it still refreshes the cookie.
        if mode == "private":
            return "resource", 200, {"Cache-Control": "private"}
        return "resource"

    if mode == "plain":
        status, headers, body = _wsgi(app, "/plain", "GET", None)
    else:
        _status, established, _body = _wsgi(app, "/establish", "POST", None)
        token = _session_token(_header_pairs(established))
        if not token:
            raise RuntimeError("establish did not set a session cookie")
        status, headers, body = _wsgi(app, "/resource", "GET", f"{_COOKIE_NAME}={token}")
    return {
        "status": status,
        "headers": headers,
        "set_cookie": _session_token(_header_pairs(headers)),
        "body": body,
    }


class World(BaseWorld):
    def exchange(self, mode: str = "refresh"):
        """Run ``mode`` and record a cross-client cache hit when the model says so.

        ``refresh`` is the CVE flow. ``private`` is the same flow with
        ``Cache-Control: private``. ``plain`` is one ordinary 200 response.
        Returns the child outcome. ``outcome.leaked`` is true only when the
        cache model delivers the victim cookie to tenant-a.
        """
        if mode not in _MODES:
            raise ValueError(f"mode must be one of {sorted(_MODES)}")
        outcome = self.call(_exchange, mode)
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
