"""Two-stage gVisor sandbox (owner: P1-2).

Stage ``dependencies``: a disposable sandbox whose only egress is the allowlist proxy
to PyPI; no credentials; output is a wheelhouse plus a hash manifest.
Stage ``execute``: a fresh sandbox with no network, installing from the wheelhouse
only; fixtures and observers mounted read-only; the challenger writes only under
``challenge_tests/``. Every exit path tears the sandbox down and records digests.
"""

from __future__ import annotations

from challenge_runner.sandbox.docker import SandboxUnavailable
from challenge_runner.sandbox.runner import run_job

__all__ = ["SandboxUnavailable", "run_job"]
