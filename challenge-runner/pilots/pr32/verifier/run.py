"""Verifier entry point: ``python /verifier/run.py [test paths]``.

Runs pytest from /work with the verifier plugin. With no paths it runs the
verifier's own controls, which is what the baseline stages record.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(os.environ.get("SBC_VERIFIER_ROOT", "/verifier"))
WORK = Path(os.environ.get("SBC_WORK_DIR", "/work"))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://verifier:verifier@127.0.0.1:9/verifier")
os.environ.setdefault("MARKETPLACE_ENABLED", "true")
os.environ.setdefault("SECRET_KEY", "verifier-only-not-a-secret")
os.environ.setdefault("JWT_SECRET_KEY", "verifier-only-not-a-secret")
os.environ["CHALLENGE_ENGINE_ENABLED"] = "false"
sys.path[:0] = [str(ROOT), str(WORK / "backend")]


def main(argv: list[str]) -> int:
    import pytest

    paths = argv or [str(ROOT / "controls")]
    args = [
        "-q",
        "-c",
        str(ROOT / "pytest.ini"),
        "--rootdir",
        str(WORK),
        "-p",
        "sbc_verifier.plugin",
        "-p",
        "no:cacheprovider",
        *paths,
    ]
    os.chdir(WORK)
    return int(pytest.main(args))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
