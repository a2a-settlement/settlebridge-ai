"""Keep a stand-in from being collected with the checkout's test suite."""

from __future__ import annotations

import os
from pathlib import Path

_MARKER = "sbc-collect: checkout test suite is not loaded"


def neutralize() -> None:
    work = os.environ.get("SBC_WORK_DIR")
    if not work:
        return
    path = Path(work) / "conftest.py"
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8")
    if _MARKER in text or "pytest_plugins" not in text:
        return
    path.write_text(f'"""{_MARKER}"""\n', encoding="utf-8")
    cache = path.parent / "__pycache__"
    if cache.is_dir():
        for item in cache.glob("conftest*.pyc"):
            item.unlink()


neutralize()
