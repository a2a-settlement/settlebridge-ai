"""Verifier world for babel-locale-path-traversal (injection_execution).

The property: Babel loads locale data only from its own locale directory,
whatever locale identifier it is given.

``exists`` and ``load`` each perform one locale-data call in a forked child
that imports Babel fresh from the checkout. The child returns the path strings
those functions passed to ``os.path.exists`` or ``open``. The parent parses
each path and records a file sentinel only when the parsed path lies outside
the library's locale directory. A test assertion is not the verdict. The child
does not open or stat a path outside the Babel package directory.
"""

from __future__ import annotations

import os
import sys

from sbc_kit import capture
from sbc_kit.worldbase import BaseWorld, require_text

MAX_NAME_BYTES = 4 * 1024
MAX_PATHS = 32
_LOCALE_MODULE = "babel.localedata"


def _path_str(value: object) -> str | None:
    if isinstance(value, int):
        return None
    try:
        text = os.fspath(value)  # type: ignore[arg-type]
    except TypeError:
        return None
    if isinstance(text, bytes):
        return os.fsdecode(text)
    if isinstance(text, str):
        return text
    return None


def _outside(root: str, candidate: str) -> bool:
    """True when ``candidate`` is not lexically inside ``root``."""
    base = os.path.normpath(root)
    path = candidate if os.path.isabs(candidate) else os.path.join(base, candidate)
    path = os.path.normpath(path)
    if path == base:
        return False
    return not path.startswith(base + os.sep)


def _record(seen: list[str], value: object) -> None:
    """Append a locale-module path. Call only directly from a filesystem wrapper."""
    if len(seen) >= MAX_PATHS:
        return
    # 0 this function, 1 the wrapper, 2 the library frame that asked.
    module = sys._getframe(2).f_globals.get("__name__", "")
    if module != _LOCALE_MODULE:
        return
    text = _path_str(value)
    if text is not None:
        seen.append(text)


def _short_error(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {str(exc)[:300]}"


def _touchable(package_dir: str, text: str) -> bool:
    if "\x00" in text:
        return False
    return not _outside(package_dir, text)


def _exists(name: str) -> dict:
    import babel.localedata as localedata

    locale_dir = os.path.abspath(localedata._dirname)
    package_dir = os.path.dirname(locale_dir)
    seen: list[str] = []
    real_exists = os.path.exists

    def tracking_exists(path, *args, **kwargs):
        _record(seen, path)
        text = _path_str(path)
        if text is not None and not _touchable(package_dir, text):
            return False
        return real_exists(path, *args, **kwargs)

    os.path.exists = tracking_exists
    available = False
    error = None
    try:
        available = bool(localedata.exists(name))
    except Exception as exc:  # noqa: BLE001
        error = _short_error(exc)
    finally:
        os.path.exists = real_exists
    return {
        "available": available,
        "error": error,
        "locale_dir": locale_dir,
        "paths": seen,
    }


def _load(name: str, merge_inherited: bool) -> dict:
    import builtins

    import babel.localedata as localedata

    locale_dir = os.path.abspath(localedata._dirname)
    package_dir = os.path.dirname(locale_dir)
    seen: list[str] = []
    real_open = builtins.open

    def tracking_open(file, mode="r", *args, **kwargs):
        _record(seen, file)
        text = _path_str(file)
        if text is not None and not _touchable(package_dir, text):
            raise FileNotFoundError(text)
        return real_open(file, mode, *args, **kwargs)

    builtins.open = tracking_open
    loaded = False
    error = None
    try:
        localedata.load(name, merge_inherited)
        loaded = True
    except Exception as exc:  # noqa: BLE001
        error = _short_error(exc)
    finally:
        builtins.open = real_open
    return {
        "loaded": loaded,
        "error": error,
        "locale_dir": locale_dir,
        "paths": seen,
    }


def _mark(outcome) -> None:
    """Parse returned paths and record a file sentinel when one leaves the directory."""
    outcome.outside = False
    if outcome.status != "ok" or not isinstance(outcome.value, dict):
        return
    locale_dir = outcome.value.get("locale_dir")
    paths = outcome.value.get("paths")
    if not isinstance(locale_dir, str) or not locale_dir or not isinstance(paths, list):
        return
    for path in paths:
        if not isinstance(path, str) or not _outside(locale_dir, path):
            continue
        outcome.outside = True
        capture.injection(
            "file",
            location="babel.localedata: locale path outside locale directory",
        )
        return


class World(BaseWorld):
    def exists(self, name: str):
        """Check whether locale data exists for ``name``.

        Returns the child outcome. ``outcome.outside`` is true only when the
        path Babel passed to ``os.path.exists`` parses as outside its locale
        directory.
        """
        checked = require_text(name, "name", MAX_NAME_BYTES)
        outcome = self.call(_exists, checked)
        _mark(outcome)
        return outcome

    def load(self, name: str, merge_inherited: bool = True):
        """Load locale data for ``name``.

        ``merge_inherited`` is passed through to Babel. Returns the child
        outcome. ``outcome.outside`` is true only when a path Babel passed to
        ``open`` while loading that locale parses as outside its locale
        directory. The locale mapping itself is not returned.
        """
        checked = require_text(name, "name", MAX_NAME_BYTES)
        if type(merge_inherited) is not bool:
            raise TypeError("merge_inherited must be a bool")
        outcome = self.call(_load, checked, merge_inherited)
        _mark(outcome)
        return outcome
