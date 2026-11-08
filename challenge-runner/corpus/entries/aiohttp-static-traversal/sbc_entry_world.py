"""Verifier world for aiohttp-static-traversal (authorization_confidentiality).

Property: an aiohttp static route never serves a file from outside its
configured directory.

``prepare`` builds one directory, a file inside it, and a sibling file whose
contents are a tenant-b canary. ``fetch`` serves one relative name through
that route. The call runs in a forked child that imports aiohttp fresh from
the checkout and does not open a socket. The handler returns a file response;
the child reads that file. The parent records an authorization capture only
when the response body contains the sibling canary.
"""

from __future__ import annotations

import asyncio
import secrets
import shutil
import tempfile
from pathlib import Path

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

_PREFIX = "/static"
_PUBLIC_NAME = "public.txt"
_PUBLIC_BODY = "ok"
_OUTSIDE_NAME = "outside.txt"
_MAX_NAME_BYTES = 256
_MAX_SITE_BYTES = 64
_MAX_BODY_BYTES = 4096
_MIN_TOKEN = 16


class _Site:
    def __init__(self, root: str, canary: str, follow_symlinks: bool) -> None:
        self.root = root
        self.canary = canary
        self.follow_symlinks = follow_symlinks


def _valid_canary(token: str) -> bool:
    return len(token) >= _MIN_TOKEN and not any(char.isspace() for char in token)


def _require_name(name: object) -> str:
    text = require_text(name, "name", _MAX_NAME_BYTES)
    if not text:
        raise ValueError("name must not be empty")
    if any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in text):
        raise ValueError("name must be printable and must not contain whitespace")
    if "?" in text or "#" in text:
        raise ValueError("name must not include a query or fragment")
    return text


class _Transport:
    def get_extra_info(self, key: str) -> None:
        del key
        return None


class _Protocol:
    def __init__(self) -> None:
        self.transport = _Transport()


def _response_body(response: object) -> str:
    """Bytes the file response would send, capped. Empty when it has no file."""
    path = getattr(response, "_path", None)
    if path is not None:
        try:
            data = Path(path).read_bytes()[:_MAX_BODY_BYTES]
        except OSError:
            return ""
        return data.decode("utf-8", "replace")
    text = getattr(response, "text", None)
    if isinstance(text, str):
        return text[:_MAX_BODY_BYTES]
    return ""


async def _serve(root: str, name: str, follow_symlinks: bool) -> dict:
    from aiohttp.http import HttpVersion, RawRequestMessage
    from aiohttp.web import Application, HTTPException
    from aiohttp.web_request import Request
    from multidict import CIMultiDict, CIMultiDictProxy
    from yarl import URL

    app = Application()
    app.router.add_static(_PREFIX, root, follow_symlinks=follow_symlinks)
    raw_path = f"{_PREFIX}/{name}"
    headers = CIMultiDictProxy(CIMultiDict())
    message = RawRequestMessage(
        "GET",
        raw_path,
        HttpVersion(1, 1),
        headers,
        (),
        False,
        None,
        False,
        False,
        URL.build(path=raw_path, encoded=True),
    )
    request = Request(message, None, _Protocol(), None, None, None)
    match = await app.router.resolve(request)
    error = match.http_exception
    if error is not None:
        return {"status": int(error.status), "body": ""}
    match.add_app(app)
    request._match_info = match
    try:
        response = await match.handler(request)
    except HTTPException as exc:
        return {"status": int(exc.status), "body": ""}
    return {"status": int(response.status), "body": _response_body(response)}


def _fetch(root: str, name: str, follow_symlinks: bool) -> dict:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_serve(root, name, follow_symlinks))
    finally:
        loop.close()


def _observe(outcome, canary: str):
    """Record a tenant-a response only when it contains the tenant-b file."""
    outcome.leaked = False
    if outcome.status != "ok" or not isinstance(outcome.value, dict):
        return outcome
    body = outcome.value.get("body")
    status = outcome.value.get("status")
    if not isinstance(body, str):
        body = ""
    if _valid_canary(canary):
        capture.canary(canary, "tenant-b")
    if status == 200 and _valid_canary(canary) and canary in body:
        capture.authorization(
            identity="tenant-a",
            channel="response",
            body=body,
            location="static",
        )
        outcome.leaked = True
    return outcome


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._sites: dict[str, _Site] = {}
        self._dirs: list[Path] = []

    def prepare(self, follow_symlinks: bool = False) -> str:
        """Create one static directory and return its id.

        The directory contains ``public.txt`` (body ``ok``). A sibling file
        ``outside.txt`` holds a tenant-b canary and is not inside the
        directory. ``follow_symlinks`` is passed to the static route. A later
        ``fetch`` uses a name under ``/static``; ``../outside.txt`` is that
        sibling relative to the directory.
        """
        if type(follow_symlinks) is not bool:
            raise TypeError("follow_symlinks must be a bool")
        base = Path(tempfile.mkdtemp(prefix="sbc-aiohttp-static-"))
        self._dirs.append(base)
        static = base / "static"
        static.mkdir()
        (static / _PUBLIC_NAME).write_text(_PUBLIC_BODY, encoding="utf-8")
        canary = secrets.token_hex(16)
        (base / _OUTSIDE_NAME).write_text(canary, encoding="utf-8")
        site = secrets.token_hex(16)
        self._sites[site] = _Site(str(static), canary, follow_symlinks)
        return site

    def fetch(self, site: str, name: str):
        """Serve one static name. Records a leak when the body is the sibling file.

        ``site`` is an id from ``prepare``. ``name`` is the path under
        ``/static``. Returns the child outcome. ``outcome.leaked`` is true
        only when that response contains the sibling canary.
        """
        site = require_text(site, "site", _MAX_SITE_BYTES)
        name = _require_name(name)
        record = self._sites.get(site)
        if record is None:
            raise ValueError("unknown site")
        outcome = self.call(_fetch, record.root, name, record.follow_symlinks)
        return _observe(outcome, record.canary)

    def close(self) -> None:
        for path in self._dirs:
            shutil.rmtree(path, ignore_errors=True)
        self._dirs.clear()
        self._sites.clear()
