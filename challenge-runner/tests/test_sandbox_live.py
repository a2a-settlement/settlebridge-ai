"""Real docker + runsc. Opt-in: CHALLENGE_SANDBOX_LIVE=1 and a pinned CHALLENGE_SANDBOX_IMAGE.

Uses the network (PyPI) for the dependency stage.
"""

from __future__ import annotations

import io
import os
import tarfile
from pathlib import Path

import pytest

from challenge_runner.artifacts import ArtifactStore
from challenge_runner.protocol import RunnerJob
from challenge_runner.sandbox import run_job

pytestmark = pytest.mark.skipif(
    os.environ.get("CHALLENGE_SANDBOX_LIVE") != "1", reason="needs docker with runsc"
)

ISOLATION_TESTS = """
import os, socket

def test_no_network():
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=3)
    except OSError:
        return
    raise AssertionError("network reachable from execute stage")

def test_unprivileged_read_only_root():
    assert os.getuid() == 65534
    try:
        open("/etc/sbx-probe", "w")
    except OSError:
        return
    raise AssertionError("root filesystem writable")
"""

LIMITS = {
    "cpu_seconds": 60,
    "wall_seconds": 20,
    "memory_mb": 512,
    "disk_mb": 256,
    "max_processes": 64,
}
TEST = ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]
INSTALL = ["pip", "install", "-r", "requirements.txt"]


def _tarball(store: ArtifactStore, tests: str) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in {"requirements.txt": "pytest==8.3.3\n", "test_x.py": tests}.items():
            data = body.encode()
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return store.put_bytes(buf.getvalue())


def _job(stage: str, source: str, n: str, wheelhouse: str | None = None) -> RunnerJob:
    return RunnerJob.from_dict(
        {
            "execution_id": f"live-{stage}-{n}",
            "stage": stage,
            "task_id": "live",
            "source_tarball": source,
            "limits": LIMITS,
            "install_command": INSTALL,
            "test_command": TEST,
            "wheelhouse_manifest": wheelhouse,
        }
    )


def test_two_stage_run_isolation_and_limits(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    source = _tarball(store, ISOLATION_TESTS)
    deps = run_job(_job("dependencies", source, "0001"), artifact_root=tmp_path)
    assert deps.status == "ok", deps.error
    assert deps.wheelhouse_manifest

    ran = run_job(_job("execute", source, "0001", deps.wheelhouse_manifest), artifact_root=tmp_path)
    assert ran.status == "ok", store.get_path(ran.log_digest).read_text()
    assert "2 passed" in store.get_path(ran.log_digest).read_text()

    failing = _tarball(store, "def test_fail():\n    assert False\n")
    failed = run_job(
        _job("execute", failing, "0002", deps.wheelhouse_manifest), artifact_root=tmp_path
    )
    assert (failed.status, failed.exit_code) == ("test_failed", 1)

    slow = _tarball(store, "import time\n\ndef test_slow():\n    time.sleep(120)\n")
    timed = run_job(_job("execute", slow, "0003", deps.wheelhouse_manifest), artifact_root=tmp_path)
    assert timed.status == "limit_exceeded"
    assert timed.wall_seconds <= LIMITS["wall_seconds"] + 15
