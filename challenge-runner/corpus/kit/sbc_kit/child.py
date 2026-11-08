"""Run one library call in a forked child against a clean import of the library.

The child drops every module the test session imported from the library packages
and from ``challenge_tests/``, blocks imports from ``challenge_tests/``, checks the
library source digest taken at session start, and only then imports the library
and runs the call. Process-spawning audit events, and ``exec`` of code compiled
from a string, are recorded while the call runs.
"""

from __future__ import annotations

import hashlib
import importlib.abc
import importlib.machinery
import json
import os
import select
import signal
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

WORK = Path(os.environ.get("SBC_WORK_DIR", "/work"))
TESTS = WORK / "challenge_tests"
MAX_REPORT = 256 * 1024
MAX_EVENTS = 32
_SPAWN_EVENTS = frozenset(
    {
        "os.system",
        "os.exec",
        "os.posix_spawn",
        "os.spawn",
        "os.fork",
        "os.forkpty",
        "subprocess.Popen",
        "pty.spawn",
        "ctypes.dlopen",
    }
)


class LibraryChanged(RuntimeError):
    """The library source under /work changed after the session started."""


@dataclass
class Outcome:
    status: str
    value: object = None
    error: str | None = None
    cpu_seconds: float = 0.0
    wall_seconds: float = 0.0
    killed: bool = False
    events: list[str] = field(default_factory=list)


def source_digest(roots: list[Path]) -> str:
    hasher = hashlib.sha256()
    for root in roots:
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            hasher.update(str(path.relative_to(root)).encode())
            hasher.update(path.read_bytes())
    return hasher.hexdigest()


def _under(path: object, root: Path) -> bool:
    if not isinstance(path, str):
        return False
    try:
        return Path(path).resolve().is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


class _BlockChallengeTests(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path, target=None):
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is not None and _under(spec.origin, TESTS):
            raise ImportError(f"{name} is outside the library under test")
        return None


def _isolate(packages: list[str]) -> None:
    for name, module in list(sys.modules.items()):
        top = name.split(".", 1)[0]
        if top in packages or _under(getattr(module, "__file__", None), TESTS):
            del sys.modules[name]
    sys.path[:] = [p for p in sys.path if not _under(p or ".", TESTS) and p not in ("", ".")]
    sys.meta_path.insert(0, _BlockChallengeTests())


def _jsonable(value: object) -> object:
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"type": type(value).__name__, "repr": repr(value)[:4000]}


def run(
    fn,
    *args,
    packages: list[str],
    source_roots: list[Path],
    expected_digest: str,
    timeout: float,
    audit: bool = False,
) -> Outcome:
    read_fd, write_fd = os.pipe()
    started = time.monotonic()
    pid = os.fork()
    if pid == 0:
        os.close(read_fd)
        report: dict = {"status": "error", "events": []}
        try:
            if source_digest(source_roots) != expected_digest:
                raise LibraryChanged("library source changed during the session")
            _isolate(packages)
            for name in packages:
                importlib.import_module(name)
            events: list[str] = report["events"]
            if audit:

                def hook(event, hook_args):
                    if len(events) >= MAX_EVENTS:
                        return
                    if event in _SPAWN_EVENTS:
                        events.append(event)
                    elif event == "exec" and hook_args:
                        filename = getattr(hook_args[0], "co_filename", "")
                        if isinstance(filename, str) and filename.startswith("<"):
                            events.append(f"exec {filename}")

                sys.addaudithook(hook)
            value = fn(*args)
            report.update(status="ok", value=_jsonable(value))
        except LibraryChanged as exc:
            report.update(status="library_changed", error=str(exc))
        except BaseException as exc:  # noqa: BLE001
            report.update(status="error", error=f"{type(exc).__name__}: {str(exc)[:500]}")
        try:
            blob = json.dumps(report).encode()[:MAX_REPORT]
            os.write(write_fd, blob)
        finally:
            os._exit(0)
    os.close(write_fd)
    chunks: list[bytes] = []
    killed = False
    deadline = started + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            os.kill(pid, signal.SIGKILL)
            killed = True
            break
        ready, _, _ = select.select([read_fd], [], [], remaining)
        if not ready:
            continue
        chunk = os.read(read_fd, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    os.close(read_fd)
    _pid, _status, usage = os.wait4(pid, 0)
    outcome = Outcome(
        status="killed" if killed else "error",
        cpu_seconds=usage.ru_utime + usage.ru_stime,
        wall_seconds=time.monotonic() - started,
        killed=killed,
    )
    if not killed:
        try:
            report = json.loads(b"".join(chunks) or b"{}")
        except json.JSONDecodeError:
            report = {"status": "error", "error": "child report unreadable"}
        outcome.status = str(report.get("status", "error"))
        outcome.value = report.get("value")
        outcome.error = report.get("error")
        outcome.events = [str(item) for item in report.get("events", [])][:MAX_EVENTS]
    return outcome
