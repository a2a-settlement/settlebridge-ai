"""The backend starts, and non-engine routes exist, with the private verifier absent.

Runs in a fresh interpreter so the import is cold: nothing another test left in
``sys.modules`` can hide a load-time import of the private package.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1]

_PROBE = r"""
import importlib.abc
import json
import sys

attempts = []


class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if fullname.split(".")[0] in {"settlebridge_verifier", "challenge_runner"}:
            attempts.append(fullname)
            raise ModuleNotFoundError(fullname)
        return None


sys.meta_path.insert(0, Block())

from app.main import app
from app.config import settings
from app.services import challenge_orchestrator, verifier_client

at_import = list(attempts)
paths = sorted(app.openapi()["paths"])

from fastapi.testclient import TestClient

# No lifespan: background loops and the database are not part of this check.
health = TestClient(app).get("/health")
print(
    json.dumps(
        {
            "attempts_at_import": at_import,
            "loaded": sorted(
                name
                for name in sys.modules
                if name.split(".")[0] in {"settlebridge_verifier", "challenge_runner"}
            ),
            "old_module_loaded": "app.services.challenge_validity" in sys.modules,
            "paths": paths,
            "health_status": health.status_code,
            "engine_enabled": settings.CHALLENGE_ENGINE_ENABLED,
            "rule_available": verifier_client.validity_available(),
            "orchestrator": challenge_orchestrator.__name__,
        }
    )
)
"""


def test_app_imports_with_the_private_verifier_unimportable():
    done = subprocess.run(
        [sys.executable, "-c", _PROBE],
        cwd=_BACKEND,
        # Mount every router, as the marketplace deployment does.
        env={**os.environ, "MARKETPLACE_ENABLED": "true"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode == 0, done.stderr[-2000:]
    report = json.loads(done.stdout.strip().splitlines()[-1])

    # Importing the app never reaches for the private packages.
    assert report["attempts_at_import"] == []
    assert report["loaded"] == []
    assert report["old_module_loaded"] is False
    assert report["orchestrator"] == "app.services.challenge_orchestrator"
    # The engine is off by default and the rule reports itself unavailable.
    assert report["engine_enabled"] is False
    assert report["rule_available"] is False
    # Non-engine routes are registered, and so are the engine's own (they answer
    # 503 while the engine is off).
    paths = report["paths"]
    assert report["health_status"] == 200
    assert any(path.startswith("/api/v1/challenges") for path in paths)
    assert any(path.startswith("/api/bounties") for path in paths)
    assert any(path.startswith("/api/v1/github") for path in paths)


def test_the_public_copy_of_the_rule_is_gone():
    assert not (_BACKEND / "app" / "services" / "challenge_validity.py").exists()
