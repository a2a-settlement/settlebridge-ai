"""Verifier entry point: ``python /verifier/run.py [test paths]``.

Runs pytest from /work with the corpus kit plugin. With no paths it runs the
verifier's own controls, which is what the baseline stages record.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("SBC_VERIFIER_ROOT", "/verifier"))
WORK = Path(os.environ.get("SBC_WORK_DIR", "/work"))


def main(argv: list[str]) -> int:
    entry = json.loads((ROOT / "entry.json").read_text(encoding="utf-8"))
    sys.path[:0] = [str(ROOT), *(str(WORK / rel) for rel in entry["source_paths"])]
    os.environ["PYTHONPATH"] = os.pathsep.join(sys.path)

    import pytest

    paths = argv or [str(ROOT / "controls")]
    args = [
        "-q",
        "-c",
        str(ROOT / "pytest.ini"),
        "--rootdir",
        str(WORK),
        "-p",
        "sbc_kit.plugin",
        "-p",
        "no:cacheprovider",
        *paths,
    ]
    os.chdir(WORK)
    return int(pytest.main(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
