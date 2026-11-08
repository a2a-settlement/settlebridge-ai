"""Verifier world for tornado-static-open-redirect (integrity).

Property: Tornado's StaticFileHandler never redirects to a different host.

``prepare`` creates a private directory tree and returns its URL paths.
``fetch`` serves one GET of one of those paths. The application is Tornado's
own static-file setup: ``static_path`` is ``/``, ``static_url_prefix`` is
``/``, and ``default_filename`` is set, so a directory that does not end in
``/`` is redirected by appending ``/`` to the request path. Each ``fetch``
imports tornado in a forked child. The parent reads ``Location`` with
``urlsplit`` and records an unauthorized redirect only when that header names
a host other than this application's host.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from urllib.parse import urlsplit

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

_APP_HOST = "app.example"
_FILE_BODY = "readme"
_INDEX_BODY = "index"
_MAX_PATH_BYTES = 512
_MAX_BODY_BYTES = 4096


def _filesystem_target(path: str) -> str:
    """File path a static URL names, dropping one extra leading slash and a trailing slash."""
    target = path[1:] if path.startswith("//") else path
    if len(target) > 1 and target.endswith("/"):
        target = target[:-1]
    return target


def _header_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return ""


def _foreign_host(location: str) -> str | None:
    """Hostname named by ``Location``, when it is not this application's host.

    A path-only Location has no host. ``urlsplit`` is the parser.
    """
    if not location or any(char.isspace() for char in location):
        return None
    try:
        host = urlsplit(location).hostname
    except ValueError:
        return None
    if not host or host.casefold() == _APP_HOST.casefold():
        return None
    return host.casefold()


def _fetch(path: str, root: str) -> dict:
    """One GET. Tornado is imported in this forked child."""
    import logging

    from tornado.httputil import HTTPHeaders, HTTPServerRequest
    from tornado.ioloop import IOLoop
    from tornado.web import Application

    for name in ("tornado.access", "tornado.application", "tornado.general"):
        logging.getLogger(name).disabled = True

    target = os.path.realpath(_filesystem_target(path))
    root_real = os.path.realpath(root)
    if target != root_real and not target.startswith(root_real + os.sep):
        raise ValueError("path is outside the static tree")

    app = Application(
        [],
        static_path="/",
        static_url_prefix="/",
        static_handler_args={"default_filename": "index.html"},
    )

    class _Conn:
        def __init__(self) -> None:
            self.status: int | None = None
            self.headers: list[tuple[object, object]] = []
            self.chunks: list[bytes] = []

        def write_headers(self, start_line, headers, chunk):
            from tornado.concurrent import Future

            self.status = start_line.code
            self.headers = list(headers.get_all())
            if chunk:
                self.chunks.append(chunk)
            future = Future()
            future.set_result(None)
            return future

        def write(self, chunk):
            from tornado.concurrent import Future

            if chunk:
                self.chunks.append(chunk)
            future = Future()
            future.set_result(None)
            return future

        def finish(self) -> None:
            return None

        def set_close_callback(self, callback) -> None:
            del callback

    async def run() -> _Conn:
        conn = _Conn()
        request = HTTPServerRequest(
            method="GET",
            uri=path,
            headers=HTTPHeaders({"Host": _APP_HOST}),
            connection=conn,
        )
        delegate = app.find_handler(request)
        handler = delegate.handler_class(app, request, **delegate.handler_kwargs)
        await handler._execute([], *delegate.path_args, **delegate.path_kwargs)
        return conn

    loop = IOLoop()
    try:
        conn = loop.run_sync(run)
    finally:
        loop.close()

    if not isinstance(conn.status, int):
        raise RuntimeError("StaticFileHandler did not write a response")
    location = ""
    for name, value in conn.headers:
        if _header_text(name).lower() == "location":
            location = _header_text(value)
            break
    body = b"".join(chunk for chunk in conn.chunks if isinstance(chunk, bytes))
    return {
        "status": conn.status,
        "location": location,
        "body": body[:_MAX_BODY_BYTES].decode("utf-8", "replace"),
    }


class World(BaseWorld):
    def __init__(self) -> None:
        super().__init__()
        self._root: str | None = None
        self._directory: str | None = None
        self._file: str | None = None
        self._captured = False

    def prepare(self) -> dict[str, str]:
        """Create the static tree and return its single-slash URL paths.

        ``directory`` is a directory whose index body is ``index``. ``file``
        is a file whose body is ``readme``. Neither path ends in ``/`` or
        contains ``//``. A later ``fetch`` accepts those paths, the same
        paths with a trailing slash, and the same paths with one extra
        leading slash.
        """
        self.close()
        root = tempfile.mkdtemp(prefix="sbc-tornado-static-")
        self._root = root
        try:
            directory = os.path.join(root, "docs")
            os.makedirs(directory)
            file_path = os.path.join(root, "readme.txt")
            with open(os.path.join(directory, "index.html"), "w", encoding="utf-8") as handle:
                handle.write(_INDEX_BODY)
            with open(file_path, "w", encoding="utf-8") as handle:
                handle.write(_FILE_BODY)
        except Exception:
            self.close()
            raise
        if (
            not directory.startswith("/")
            or directory.startswith("//")
            or "//" in directory
            or any(char.isspace() for char in directory)
            or not file_path.startswith(directory[: -len("docs")])
        ):
            self.close()
            raise RuntimeError("static tree path is not a single-slash absolute path")
        self._directory = directory
        self._file = file_path
        return {"directory": directory, "file": file_path}

    def _allowed(self, path: str) -> set[str]:
        directory = self._directory
        file_path = self._file
        if self._root is None or directory is None or file_path is None:
            raise RuntimeError("prepare() must be called before fetch()")
        return {
            directory,
            directory + "/",
            file_path,
            "/" + directory,
            "/" + directory + "/",
            "/" + file_path,
        }

    def fetch(self, path: str):
        """Serve one GET and record a redirect whose Location names another host.

        ``path`` is a path returned by ``prepare``, that path with a trailing
        slash, or that path with one extra leading slash. Returns the child
        outcome. ``outcome.value`` has ``status``, ``location``, and ``body``.
        ``outcome.off_host`` is true only when ``Location`` names a host other
        than the application host, which is also when the redirect is recorded.
        """
        supplied = require_text(path, "path", _MAX_PATH_BYTES)
        if supplied not in self._allowed(supplied):
            raise ValueError("path is outside the prepared static tree")
        root = self._root
        if root is None:
            raise RuntimeError("prepare() must be called before fetch()")
        outcome = self.call(_fetch, supplied, root)
        outcome.off_host = False
        if outcome.status != "ok" or not isinstance(outcome.value, dict):
            return outcome
        location = outcome.value.get("location")
        if not isinstance(location, str):
            return outcome
        host = _foreign_host(location)
        if host is None:
            return outcome
        outcome.off_host = True
        if not self._captured:
            self._captured = True
            capture.integrity(
                table="redirect",
                row_id="static",
                identity="client",
                authorized=False,
                before={"host": _APP_HOST},
                after={"host": host},
            )
        return outcome

    def close(self) -> None:
        root = self._root
        self._root = None
        self._directory = None
        self._file = None
        if root is not None:
            shutil.rmtree(root, ignore_errors=True)
