"""Verifier world for requests-proxy-authorization (authorization_confidentiality).

Property: requests does not forward a proxy credential to the destination
server after a redirect.

``credential`` returns tenant-b's proxy password. ``request`` performs one GET
in a forked child that imports requests from /work. An in-process transport
answers that call, so nothing is opened on the network. When ``redirect`` is
set, the first response is a redirect and the library follows it itself.

The child returns each hop's scheme, whether the library selected a proxy, and
the headers on the prepared request. The parent keeps a trusted forwarding
model: a credential on a tunneled (https) hop after the redirect is visible to
the destination; on a plaintext hop the proxy removes it before the origin.
The parent records an authorization capture only when the tunneled request
actually carries this call's proxy password. A plaintext redirect stays silent.
"""

from __future__ import annotations

import base64

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_DELIVERED_BYTES = 8192
_MIN_TOKEN = 16
_MAX_SECRET_BYTES = 128
_MAX_TARGET_BYTES = 16
_MAX_REDIRECT_BYTES = 8
_MAX_HOPS = 8
_MAX_HEADERS = 64
_MAX_HEADER_BYTES = 8192
_MAX_URL_BYTES = 2048
_PROXY_USER = "tenant-b"
_SECRET = "sbc-proxy-tenant-b-secret"
_SECRET_CHARS = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._~-")
_TARGETS = {
    "plain": "http://origin.example/plain",
    "resource": "http://origin.example/resource",
}
_REDIRECTS = {
    "http": "http://dest.example/next",
    "https": "https://dest.example/next",
}


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


def _decode_basic(value: str) -> tuple[str, str, str] | None:
    """Return ``(token, username, password)`` from a Basic credential."""
    scheme, sep, token = value.partition(" ")
    if not sep or scheme.lower() != "basic" or not _valid_canary(token):
        return None
    try:
        raw = base64.b64decode(token, validate=True)
    except (ValueError, TypeError):
        return None
    username, sep, password = raw.decode("latin1").partition(":")
    if not sep:
        return None
    return token, username, password


def _forwarded_token(hop: object, secret: str) -> str:
    """Token on one hop when the destination would see this call's password."""
    if not isinstance(hop, dict):
        return ""
    if hop.get("scheme") != "https" or hop.get("proxied") is not True:
        return ""
    for name, value in _header_pairs(hop.get("headers")):
        if name.lower() != "proxy-authorization":
            continue
        decoded = _decode_basic(value)
        if decoded is None:
            continue
        token, username, password = decoded
        if username == _PROXY_USER and password == secret:
            return token
    return ""


def _delivered(url: object, pairs: list[tuple[str, str]]) -> str:
    """Request text the destination would see, credential line kept first."""
    auth_lines: list[str] = []
    other_lines: list[str] = []
    for name, value in pairs:
        line = f"{name}: {value}"
        if name.lower() == "proxy-authorization":
            auth_lines.append(line)
        else:
            other_lines.append(line)
    text = "\n".join([str(url), *auth_lines, *other_lines])
    encoded = text.encode("utf-8", "surrogatepass")
    if len(encoded) <= MAX_DELIVERED_BYTES:
        return text
    return encoded[:MAX_DELIVERED_BYTES].decode("utf-8", "ignore")


def _require_target(target: object) -> str:
    text = require_text(target, "target", _MAX_TARGET_BYTES)
    if text not in _TARGETS:
        raise ValueError(f"target must be one of {sorted(_TARGETS)}")
    return text


def _require_secret(secret: object) -> str | None:
    if secret is None:
        return None
    text = require_text(secret, "secret", _MAX_SECRET_BYTES)
    if len(text) < _MIN_TOKEN:
        raise ValueError("secret is shorter than 16 characters")
    if any(char not in _SECRET_CHARS for char in text):
        raise ValueError("secret must be an unreserved proxy password")
    return text


def _require_redirect(redirect: object) -> str | None:
    if redirect is None:
        return None
    text = require_text(redirect, "redirect", _MAX_REDIRECT_BYTES)
    if text not in _REDIRECTS:
        raise ValueError(f"redirect must be one of {sorted(_REDIRECTS)}")
    return text


def _block_network() -> None:
    import socket

    def refuse(*_args, **_kwargs):
        raise RuntimeError("network disabled")

    socket.create_connection = refuse
    socket.socket.connect = refuse


def _request(url: str, secret: str | None, location: str | None) -> dict:
    """One GET. The library follows ``location`` when the transport returns it."""
    import io
    from urllib.parse import quote, urlparse

    _block_network()

    import requests
    from requests.adapters import BaseAdapter
    from requests.models import Response
    from requests.structures import CaseInsensitiveDict
    from requests.utils import select_proxy

    hops: list[dict] = []

    def body_for(target: str) -> bytes:
        if urlparse(target).path == "/plain":
            return b"ok"
        return b"resource"

    def respond(prepared, status: int, location_header: str | None) -> Response:
        response = Response()
        response.status_code = status
        response.reason = "Found" if status != 200 else "OK"
        response.url = prepared.url
        response.request = prepared
        response.headers = CaseInsensitiveDict()
        response.encoding = "utf-8"
        if location_header:
            response.headers["Location"] = location_header
        payload = b"" if location_header else body_for(prepared.url)
        response.raw = io.BytesIO(payload)
        return response

    class _Transport(BaseAdapter):
        def __init__(self):
            self.sent = 0

        def send(
            self,
            request,
            stream=False,
            timeout=None,
            verify=True,
            cert=None,
            proxies=None,
        ):
            del stream, timeout, verify, cert
            self.sent += 1
            pairs: list[list[str]] = []
            for name, value in request.headers.items():
                if not isinstance(name, str) or not isinstance(value, str):
                    continue
                if len(pairs) >= _MAX_HEADERS:
                    break
                if len(value.encode("utf-8", "surrogatepass")) > _MAX_HEADER_BYTES:
                    continue
                pairs.append([name, value])
            proxy = select_proxy(request.url, proxies)
            parsed = urlparse(request.url)
            if len(hops) < _MAX_HOPS:
                hops.append(
                    {
                        "url": request.url[:_MAX_URL_BYTES],
                        "scheme": parsed.scheme,
                        "proxied": bool(proxy),
                        "headers": pairs,
                    }
                )
            redirect_to = location if self.sent == 1 else None
            status = 302 if redirect_to else 200
            return respond(request, status, redirect_to)

        def close(self):
            return None

    session = requests.Session()
    session.trust_env = False
    transport = _Transport()
    session.mount("https://", transport)
    session.mount("http://", transport)
    proxies = None
    if secret:
        user = quote(_PROXY_USER, safe="")
        password = quote(secret, safe="")
        proxy_url = f"http://{user}:{password}@proxy.invalid:8080"
        proxies = {"http": proxy_url, "https": proxy_url}
    try:
        response = session.get(
            url,
            proxies=proxies,
            allow_redirects=True,
            verify=False,
            timeout=5,
        )
        raw = response.content or b""
        if not isinstance(raw, bytes):
            raw = bytes(raw)
        return {
            "status": int(response.status_code),
            "body": raw.decode("utf-8", "replace")[:4096],
            "hops": hops,
        }
    finally:
        session.close()


def _observe(outcome, secret: str | None):
    """Record a tunneled credential from the hops the child returned."""
    outcome.leaked = False
    if outcome.status != "ok" or not isinstance(outcome.value, dict) or not secret:
        if isinstance(outcome.value, dict):
            outcome.value = _public_report(outcome.value)
        return outcome
    report = outcome.value
    hops = report.get("hops")
    token = ""
    leaked_hop = None
    if isinstance(hops, list):
        for hop in hops[1:]:
            token = _forwarded_token(hop, secret)
            if token:
                leaked_hop = hop
                break
    outcome.value = _public_report(report)
    if not token or not isinstance(leaked_hop, dict):
        return outcome
    delivered = _delivered(leaked_hop.get("url"), _header_pairs(leaked_hop.get("headers")))
    if token not in delivered:
        return outcome
    capture.canary(token, "tenant-b")
    capture.authorization(
        identity="tenant-a",
        channel="response",
        body=delivered,
        location="destination",
    )
    outcome.leaked = True
    return outcome


def _public_report(report: dict) -> dict:
    status = report.get("status")
    body = report.get("body")
    if isinstance(status, bool) or not isinstance(status, int):
        status = 0
    if not isinstance(body, str):
        body = ""
    return {"status": status, "body": body}


class World(BaseWorld):
    def credential(self) -> str:
        """Return tenant-b's proxy password.

        A later ``request`` accepts this value as ``secret`` and places it in
        the proxy URL. The library is not called.
        """
        return _SECRET

    def request(
        self,
        target: str = "plain",
        secret: str | None = None,
        redirect: str | None = None,
    ):
        """Perform one GET and record a tunneled proxy credential when sent.

        ``target`` is ``plain`` (body ``ok``) or ``resource`` (body
        ``resource``). ``secret`` is a proxy password from ``credential``, or
        any other accepted password; omitted, the call does not use proxy
        authentication. ``redirect`` is ``http``, ``https``, or omitted. When
        set, the stand-in answers the first hop with that redirect and the
        library follows it. Returns the child outcome. ``outcome.leaked`` is
        true only when a hop after the redirect would deliver this password
        to the destination.
        """
        target = _require_target(target)
        secret = _require_secret(secret)
        redirect = _require_redirect(redirect)
        url = _TARGETS[target]
        location = _REDIRECTS[redirect] if redirect else None
        return _observe(self.call(_request, url, secret, location), secret)
