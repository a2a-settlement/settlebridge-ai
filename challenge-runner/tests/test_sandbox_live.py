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


def test_execute_disk_limit_is_enforced_and_cleaned_up(tmp_path: Path):
    """A write past disk_mb fails in the sandbox and leaves no docker objects."""
    store = ArtifactStore(tmp_path)
    script = (
        "from pathlib import Path\n"
        "body = Path('/work/probe.py').read_text()\n"
        "if 'ok' not in body:\n"
        "    raise SystemExit('seed missing')\n"
        "chunk = b'x' * (1024 * 1024)\n"
        "remaining = 32 * 1024 * 1024\n"
        "handle = open('/work/fill.bin', 'wb', buffering=0)\n"
        "while remaining > 0:\n"
        "    step = chunk if remaining >= len(chunk) else chunk[:remaining]\n"
        "    handle.write(step)\n"
        "    remaining -= len(step)\n"
        "handle.close()\n"
    )
    source = _tarball_files(store, {"probe.py": b"print('ok')\n"})
    manifest = store.put_bytes(b"{}")
    limits = {**LIMITS, "disk_mb": 8, "wall_seconds": 120, "cpu_seconds": 60}
    job = RunnerJob.from_dict(
        {
            "execution_id": "live-exec-disk01",
            "stage": "execute",
            "task_id": "live",
            "source_tarball": source,
            "limits": limits,
            "install_command": [],
            "test_command": ["python", "-c", script],
            "wheelhouse_manifest": manifest,
        }
    )
    result = run_job(job, artifact_root=tmp_path)
    log = ""
    if result.log_digest:
        log = store.get_path(result.log_digest).read_text(errors="replace")
    assert result.status == "limit_exceeded", log or result.error
    assert result.error == "disk limit exceeded", log
    leftovers = _docker_names("live-exec-disk01")
    assert leftovers == [], leftovers


def test_execute_seed_and_capture_roundtrip(tmp_path: Path, monkeypatch):
    """Seeded work is visible in the job, and capture comes back out."""
    seen: dict[str, str] = {}

    class _Observer:
        name = "roundtrip"

        def observe(self, capture_dir: str):
            seen["marker"] = (Path(capture_dir) / "marker.txt").read_text()
            return []

    monkeypatch.setattr("challenge_runner.observers.get_observer", lambda _name: _Observer())
    store = ArtifactStore(tmp_path)
    script = (
        "from pathlib import Path\n"
        "body = Path('/work/probe.py').read_text()\n"
        "Path('/verifier/capture/marker.txt').write_text(body)\n"
        "print(body, end='')\n"
    )
    source = _tarball_files(store, {"probe.py": b"print('ok')\n"})
    manifest = store.put_bytes(b"{}")
    limits = {**LIMITS, "disk_mb": 32, "wall_seconds": 60, "cpu_seconds": 30}
    job = RunnerJob.from_dict(
        {
            "execution_id": "live-exec-round01",
            "stage": "execute",
            "task_id": "live",
            "source_tarball": source,
            "limits": limits,
            "install_command": [],
            "test_command": ["python", "-c", script],
            "wheelhouse_manifest": manifest,
            "observers": ["roundtrip"],
        }
    )
    result = run_job(job, artifact_root=tmp_path)
    log = ""
    if result.log_digest:
        log = store.get_path(result.log_digest).read_text(errors="replace")
    assert result.status == "ok", log or result.error
    assert "print('ok')" in seen.get("marker", ""), log
    assert "print('ok')" in log
    leftovers = _docker_names("live-exec-round01")
    assert leftovers == [], leftovers


def _tarball_files(store: ArtifactStore, files: dict[str, bytes]) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, body in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
    return store.put_bytes(buf.getvalue())


def _docker_names(token: str) -> list[str]:
    import subprocess

    found: list[str] = []
    queries = (
        ["docker", "ps", "-aq", "--filter", f"name={token}", "--format", "{{.Names}}"],
        ["docker", "volume", "ls", "-q", "--filter", f"name={token}"],
        ["docker", "network", "ls", "-q", "--filter", f"name={token}"],
    )
    for argv in queries:
        completed = subprocess.run(argv, capture_output=True, text=True, check=False)
        found.extend(line.strip() for line in completed.stdout.splitlines() if line.strip())
    return found
