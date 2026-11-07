"""Two-stage gVisor sandbox (owner: P1-2).

Stage ``dependencies``: a disposable sandbox whose only egress is the allowlist proxy
to PyPI; no credentials; output is a wheelhouse plus a hash manifest.
Stage ``execute``: a fresh sandbox with no network, installing from the wheelhouse
only; fixtures and observers mounted read-only; the challenger writes only under
``challenge_tests/``. Every exit path tears the sandbox down and records digests.
"""

from __future__ import annotations

from pathlib import Path

from challenge_runner.protocol import RunnerJob, RunnerResult


class SandboxUnavailable(Exception):
    """Docker or the runsc runtime is missing; the caller reports sandbox_error."""


def run_job(job: RunnerJob, *, artifact_root: Path) -> RunnerResult:
    raise NotImplementedError
