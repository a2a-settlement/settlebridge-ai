"""Base class for an entry's ``sbc_world`` fixture."""

from __future__ import annotations

import json
import os
from pathlib import Path

from sbc_kit import child

ROOT = Path(os.environ.get("SBC_VERIFIER_ROOT", "/verifier"))
WORK = Path(os.environ.get("SBC_WORK_DIR", "/work"))

_frozen: dict = {}


def entry() -> dict:
    return json.loads((ROOT / "entry.json").read_text(encoding="utf-8"))


def _package_roots(config: dict) -> list[Path]:
    roots = []
    for package in config["packages"]:
        for rel in config["source_paths"]:
            candidate = WORK / rel / package
            if candidate.is_dir():
                roots.append(candidate)
                break
            if (WORK / rel / f"{package}.py").is_file():
                roots.append(WORK / rel)
                break
        else:
            raise RuntimeError(f"package {package} not found under {config['source_paths']}")
    return roots


def freeze() -> None:
    """Take the library source digest before any test module is imported."""
    config = entry()
    roots = _package_roots(config)
    _frozen.update(config=config, roots=roots, digest=child.source_digest(roots))


def require_text(value: object, name: str, max_bytes: int) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be a plain str")
    if len(value.encode("utf-8", "surrogatepass")) > max_bytes:
        raise ValueError(f"{name} is longer than {max_bytes} bytes")
    return value


def require_bytes(value: object, name: str, max_bytes: int) -> bytes:
    if type(value) is not bytes:
        raise TypeError(f"{name} must be plain bytes")
    if len(value) > max_bytes:
        raise ValueError(f"{name} is longer than {max_bytes} bytes")
    return value


class BaseWorld:
    child_timeout = 20.0

    def __init__(self):
        if not _frozen:
            freeze()
        self.entry = _frozen["config"]

    def call(self, fn, *args, audit: bool = False, timeout: float | None = None) -> child.Outcome:
        outcome = child.run(
            fn,
            *args,
            packages=list(self.entry["packages"]),
            source_roots=list(_frozen["roots"]),
            expected_digest=_frozen["digest"],
            timeout=timeout or self.child_timeout,
            audit=audit,
        )
        if outcome.status == "library_changed":
            raise child.LibraryChanged(outcome.error or "library source changed")
        return outcome

    def close(self) -> None:
        pass
