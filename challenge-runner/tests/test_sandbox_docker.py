"""Two-stage sandbox argv, teardown, tar rejection, and log sanitizer.

Docker is not installed here. Every test mocks the subprocess layer.
"""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from challenge_runner.artifacts import ArtifactStore
from challenge_runner.protocol import DIGEST_RE, ObserverEvent, RunnerJob
from challenge_runner.sandbox import docker as docker_mod
from challenge_runner.sandbox import run_job
from challenge_runner.sandbox.docker import TMPFS_SPEC, sanitize_execution_id
from challenge_runner.sandbox.sanitize import MAX_LOG_BYTES, parse_canaries, scrub
from challenge_runner.sandbox.stage_deps import build_pip_invocations
from challenge_runner.sandbox.stage_exec import pip_install_argv

IMAGE = "ghcr.io/acme/runner@sha256:" + "ab" * 32
INSPECTED = "ghcr.io/acme/runner@sha256:" + "cd" * 32
IMAGE_DIGEST = "sha256:" + "cd" * 32
SECRET = "ghs_supersecretvalue1234"
CANARY = "CANARY_VALUE_12345678"
WHEEL_NAME = "demo-1.0-py3-none-any.whl"
WHEEL_BYTES = b"wheel-bytes"
HOST_TOKEN = "ghs_shouldnotleak123456"


class FakeProc:
    def __init__(self, fake: "FakeDocker", argv: list[str]):
        self.fake = fake
        self.argv = argv
        self.returncode = None
        self._calls = 0

    def communicate(self, timeout=None):
        self._calls += 1
        if self.fake.mode == "explode":
            raise RuntimeError("docker exploded")
        if self.fake.mode == "timeout" and self._calls == 1 and timeout is not None:
            raise __import__("subprocess").TimeoutExpired(self.argv, timeout)
        self.returncode = 137 if self.fake.mode == "oom" else self.fake.exit_code
        if self.fake.mode == "ok" and _is_deps(self.argv):
            _write_wheel(self.argv)
        return self.fake.stdout, self.fake.stderr

    def kill(self):
        self.returncode = -9


class FakeDocker:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.mode = "ok"
        self.exit_code = 0
        self.stdout = f"log {SECRET} {CANARY}\n"
        self.stderr = ""
        self.rm_fail = False
        self.runtimes = {"runc": {"path": "runc"}, "runsc": {"path": "/usr/bin/runsc"}}
        self.created_net: str | None = None
        self.shells: list[bool] = []

    def run(self, argv, shell=False, capture_output=False, text=False, timeout=None, check=False):
        import subprocess

        argv = list(argv)
        self.calls.append(argv)
        self.shells.append(bool(shell))
        if argv[1:3] == ["image", "inspect"]:
            return subprocess.CompletedProcess(argv, 0, INSPECTED + "\n", "")
        if argv[1] == "info":
            body = json.dumps(self.runtimes)
            code = 0 if self.runtimes is not None else 1
            return subprocess.CompletedProcess(argv, code, body, "")
        if argv[1:3] == ["network", "create"]:
            if "--internal" in argv:
                self.created_net = argv[-1]
            return subprocess.CompletedProcess(argv, 0, "netid\n", "")
        if argv[1:3] == ["network", "connect"]:
            return subprocess.CompletedProcess(argv, 0, "", "")
        if argv[1:3] == ["network", "rm"]:
            return subprocess.CompletedProcess(argv, 0, argv[-1], "")
        if argv[1:3] == ["rm", "-f"]:
            code = 1 if self.rm_fail else 0
            err = "rm failed" if self.rm_fail else ""
            return subprocess.CompletedProcess(argv, code, "", err)
        if argv[1] == "kill":
            return subprocess.CompletedProcess(argv, 0, argv[-1], "")
        if argv[1] == "inspect":
            fmt = argv[argv.index("--format") + 1]
            if "NetworkSettings" in fmt:
                payload = {self.created_net: {"IPAddress": "172.28.0.2"}}
                return subprocess.CompletedProcess(argv, 0, json.dumps(payload), "")
            if "OOMKilled" in fmt:
                oom = "true" if self.mode == "oom" else "false"
                code = "137" if self.mode == "oom" else str(self.exit_code)
                return subprocess.CompletedProcess(argv, 0, f"{oom}|{code}\n", "")
        if argv[1] == "run" and "-d" in argv:
            return subprocess.CompletedProcess(argv, 0, "proxy\n", "")
        raise AssertionError(f"unexpected docker argv: {argv}")

    def popen(self, argv, shell=False, stdout=None, stderr=None, text=None):
        argv = list(argv)
        self.calls.append(argv)
        self.shells.append(bool(shell))
        return FakeProc(self, argv)


@pytest.fixture
def fake(monkeypatch):
    docker = FakeDocker()
    monkeypatch.setattr(docker_mod.subprocess, "run", docker.run)
    monkeypatch.setattr(docker_mod.subprocess, "Popen", docker.popen)
    monkeypatch.setenv("CHALLENGE_SANDBOX_IMAGE", IMAGE)
    monkeypatch.setenv("CHALLENGE_SANDBOX_RUNTIME", "runsc")
    monkeypatch.setenv("GH_TOKEN", HOST_TOKEN)
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "AKIAIOSFODNN7EXAMPLE")
    return docker


def _tar(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _store(root: Path, data: bytes) -> str:
    return ArtifactStore(root).put_bytes(data)


def _job(
    root: Path, *, stage: str, files: dict[str, bytes], execution_id: str = "exec-0001ab", **extra
):
    source = _store(root, _tar(files))
    limits = {
        "cpu_seconds": 30,
        "memory_mb": 512,
        "wall_seconds": 30,
        "disk_mb": 1024,
        "max_processes": 128,
    }
    data = {
        "protocol": "challenge-runner/v1",
        "execution_id": execution_id,
        "stage": stage,
        "task_id": "task-1",
        "source_tarball": source,
        "limits": limits,
        "install_command": ["pip", "install", "-r", "requirements.txt"],
        "test_command": ["pytest", "-q", "challenge_tests"],
        "allowlist_hosts": ["pypi.org", "files.pythonhosted.org"],
    }
    data.update(extra)
    return RunnerJob.from_dict(data)


def _hashed_requirements() -> str:
    digest = "ab" * 32
    return f"requests==2.31.0 --hash=sha256:{digest}\n"


def _runs(fake: FakeDocker, marker: str) -> list[list[str]]:
    return [argv for argv in fake.calls if any(marker in part for part in argv)]


def _assert_common(argv: list[str], *, name: str, memory: str, pids: str) -> None:
    assert "--rm" not in argv
    assert not any(part.startswith("--rm=") for part in argv)
    assert not any("docker.sock" in part for part in argv)
    assert "--network=host" not in argv
    assert "--privileged" not in argv
    assert "--read-only" in argv
    assert TMPFS_SPEC in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert f"--pids-limit={int(pids) + 256}" in argv
    assert argv[argv.index("--ulimit") + 1] == f"nproc={pids}:{pids}"
    assert f"--memory={memory}" in argv
    assert f"--memory-swap={memory}" in argv
    assert "--cpus=1" in argv
    assert "--user=65534:65534" in argv
    assert "--workdir=/work" in argv
    assert "--runtime=runsc" in argv
    assert argv[argv.index("--name") + 1] == name
    stop = argv.index("--stop-timeout")
    assert argv[stop + 1] == "10"
    for index, part in enumerate(argv):
        if part == "-e":
            assert "=" in argv[index + 1]
            assert HOST_TOKEN not in argv[index + 1]
            assert "AKIAIOSFODNN7EXAMPLE" not in argv[index + 1]


def test_sanitize_execution_id_replaces_colons():
    assert sanitize_execution_id("exec:0001abcd") == "exec-0001abcd"


def test_unpinned_image_is_sandbox_error(fake, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("CHALLENGE_SANDBOX_IMAGE", "python:3.11")
    job = _job(tmp_path, stage="dependencies", files={"requirements.txt": b"requests==1\n"})
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "sandbox_error"
    assert "pinned" in (result.error or "")
    assert fake.calls == []
    assert DIGEST_RE.fullmatch(result.image_digest)


def test_missing_runtime_is_sandbox_error(fake, tmp_path: Path):
    fake.runtimes = {"runc": {"path": "runc"}}
    job = _job(tmp_path, stage="dependencies", files={"requirements.txt": b"requests==1\n"})
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "sandbox_error"
    assert "runtime" in (result.error or "")
    assert fake.calls[0][:4] == ["docker", "info", "--format", "{{json .Runtimes}}"]
    assert not _runs(fake, "stage_deps")


def test_dependencies_flags_manifest_and_sanitized_log(fake, tmp_path: Path):
    canaries = _store(tmp_path, json.dumps([CANARY]).encode())
    job = _job(
        tmp_path,
        stage="dependencies",
        files={"requirements.txt": _hashed_requirements().encode()},
        execution_id="exec:0001abcd",
        canaries_digest=canaries,
    )
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "ok", result.error
    assert result.image_digest == IMAGE_DIGEST
    assert result.source_digest == job.source_tarball
    assert result.cpu_seconds >= 0
    assert result.wall_seconds >= 0
    assert fake.shells == [False] * len(fake.shells)

    create = ["docker", "network", "create", "--internal", "sbx-net-exec-0001abcd"]
    assert create in fake.calls
    deps = _runs(fake, "stage_deps")
    assert len(deps) == 1
    argv = deps[0]
    _assert_common(argv, name="sbx-exec-0001abcd", memory="512m", pids="128")
    assert "--network=sbx-net-exec-0001abcd" in argv
    assert "--network=none" not in argv
    assert "bridge" not in argv
    assert "--require-hashes" in argv
    assert "/out/wheels" in argv
    assert "HTTPS_PROXY=http://172.28.0.2:8080" in argv
    assert "HTTP_PROXY=http://172.28.0.2:8080" in argv
    proxy = _runs(fake, "challenge_runner.sandbox.proxy")
    assert proxy and "--network=sbx-net-exec-0001abcd" in proxy[0]
    assert "--network=sbx-egress-exec-0001abcd" in proxy[0]
    assert any(part.endswith(":/etc/resolv.conf:ro") for part in proxy[0])
    assert "-d" in proxy[0]
    assert ["docker", "network", "create", "sbx-egress-exec-0001abcd"] in fake.calls
    assert not any(call[1:3] == ["network", "connect"] for call in fake.calls)
    assert fake.calls[-4:] == [
        ["docker", "rm", "-f", "sbx-exec-0001abcd"],
        ["docker", "rm", "-f", "sbx-proxy-exec-0001abcd"],
        ["docker", "network", "rm", "sbx-net-exec-0001abcd"],
        ["docker", "network", "rm", "sbx-egress-exec-0001abcd"],
    ]
    manifest = json.loads(ArtifactStore(tmp_path).get_path(result.wheelhouse_manifest).read_text())
    wheel_digest = manifest[WHEEL_NAME]
    assert ArtifactStore(tmp_path).get_path(wheel_digest).read_bytes() == WHEEL_BYTES
    log = ArtifactStore(tmp_path).get_path(result.log_digest).read_text()
    assert SECRET not in log
    assert CANARY not in log
    assert HOST_TOKEN not in log
    dumped = json.dumps(result.to_dict())
    assert SECRET not in dumped
    assert CANARY not in dumped
    pip_cmds = build_pip_invocations(
        dest="/out/wheels",
        requirements=["/src/requirements.txt"],
        packages=[],
        require_hashes=True,
    )
    assert pip_cmds[0][1:4] == ["-m", "pip", "download"]
    assert pip_cmds[1][1:4] == ["-m", "pip", "wheel"]
    assert "--require-hashes" in pip_cmds[0]
    assert "--require-hashes" in pip_cmds[1]


def test_dependencies_without_hashes_omits_require_hashes(fake, tmp_path: Path):
    job = _job(tmp_path, stage="dependencies", files={"requirements.txt": b"requests==2.31.0\n"})
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "ok", result.error
    argv = _runs(fake, "stage_deps")[0]
    assert "--require-hashes" not in argv


def test_execute_uses_network_none_and_offline_install(fake, tmp_path: Path):
    wheel = _store(tmp_path, WHEEL_BYTES)
    manifest = _store(tmp_path, json.dumps({WHEEL_NAME: wheel}).encode())
    job = _job(
        tmp_path,
        stage="execute",
        files={"requirements.txt": b"demo==1.0\n", "pkg/__init__.py": b""},
        wheelhouse_manifest=manifest,
    )
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "ok", result.error
    argv = _runs(fake, "stage_exec")[0]
    _assert_common(argv, name="sbx-exec-0001ab", memory="512m", pids="128")
    assert "--network=none" in argv
    assert not any(part.startswith("--network=sbx-net-") for part in argv)
    assert "HTTPS_PROXY" not in " ".join(argv)
    assert "HTTP_PROXY" not in " ".join(argv)
    joined = " ".join(argv)
    assert "--find-links" in argv
    assert "/wheels" in joined
    assert ":/verifier:ro" in joined
    assert ":/verifier/capture:rw" in joined
    assert "pytest" in argv
    assert "challenge_tests" in argv
    assert ["docker", "network", "create", "--internal", "sbx-net-exec-0001ab"] not in fake.calls
    assert ["docker", "rm", "-f", "sbx-exec-0001ab"] in fake.calls
    install = pip_install_argv(
        find_links="/wheels",
        target="/work/.pydeps",
        requirements=["/work/requirements.txt"],
        packages=[],
        wheels=[],
    )
    assert "--no-index" in install
    assert "--find-links" in install
    assert "/wheels" in install
    assert "--index-url" not in install


@pytest.mark.parametrize(
    ("mode", "status"),
    [
        ("ok", "ok"),
        ("fail", "test_failed"),
        ("timeout", "limit_exceeded"),
        ("oom", "limit_exceeded"),
    ],
)
def test_teardown_on_success_failure_timeout_and_oom(fake, tmp_path: Path, mode, status):
    fake.mode = mode
    fake.exit_code = 0 if mode == "ok" else 1
    job = _job(
        tmp_path,
        stage="dependencies",
        files={"requirements.txt": b"requests==2.31.0\n"},
        execution_id="exec-tear0001",
    )
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == status, result.error
    names = ["sbx-exec-tear0001", "sbx-proxy-exec-tear0001"]
    assert ["docker", "rm", "-f", names[0]] in fake.calls
    assert ["docker", "rm", "-f", names[1]] in fake.calls
    assert ["docker", "network", "rm", "sbx-net-exec-tear0001"] in fake.calls
    if mode == "timeout":
        kill_at = fake.calls.index(["docker", "kill", names[0]])
        rm_at = fake.calls.index(["docker", "rm", "-f", names[0]])
        assert kill_at < rm_at


def test_teardown_on_exception_and_rm_failure_does_not_hide_result(fake, tmp_path: Path):
    fake.mode = "explode"
    job = _job(tmp_path, stage="dependencies", files={"requirements.txt": b"requests==2.31.0\n"})
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "sandbox_error"
    assert ["docker", "rm", "-f", "sbx-exec-0001ab"] in fake.calls
    assert ["docker", "network", "rm", "sbx-net-exec-0001ab"] in fake.calls

    fake.calls.clear()
    fake.mode = "ok"
    fake.rm_fail = True
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "ok", result.error
    log = ArtifactStore(tmp_path).get_path(result.log_digest).read_text()
    assert "[teardown]" in log
    assert "rm failed" in log


def test_challenger_bundle_outside_challenge_tests_is_rejected(fake, tmp_path: Path):
    wheel = _store(tmp_path, WHEEL_BYTES)
    manifest = _store(tmp_path, json.dumps({WHEEL_NAME: wheel}).encode())
    bundle = _store(tmp_path, _tar({"setup.py": b"print('escape')\n"}))
    job = _job(
        tmp_path,
        stage="execute",
        files={"requirements.txt": b"demo==1.0\n"},
        wheelhouse_manifest=manifest,
        challenger_bundle=bundle,
    )
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "sandbox_error"
    assert "challenge_tests" in (result.error or "")
    assert _runs(fake, "stage_exec") == []


def test_observer_unavailable_is_sandbox_error(fake, tmp_path: Path, monkeypatch):
    wheel = _store(tmp_path, WHEEL_BYTES)
    manifest = _store(tmp_path, json.dumps({WHEEL_NAME: wheel}).encode())

    def missing(_name):
        raise NotImplementedError

    monkeypatch.setattr("challenge_runner.observers.get_observer", missing)
    job = _job(
        tmp_path,
        stage="execute",
        files={"requirements.txt": b"demo==1.0\n"},
        wheelhouse_manifest=manifest,
        observers=["authorization_canary"],
    )
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "sandbox_error"
    assert result.error == "observer unavailable"
    assert ["docker", "rm", "-f", "sbx-exec-0001ab"] in fake.calls


def test_observer_events_are_recorded(fake, tmp_path: Path, monkeypatch):
    wheel = _store(tmp_path, WHEEL_BYTES)
    manifest = _store(tmp_path, json.dumps({WHEEL_NAME: wheel}).encode())

    class _Observer:
        name = "authorization_canary"

        def observe(self, capture_dir: str):
            assert Path(capture_dir).name == "capture"
            return [
                ObserverEvent(
                    observer=self.name,
                    kind="canary_seen",
                    detail="tenant-B canary",
                    location="GET /api",
                )
            ]

    monkeypatch.setattr("challenge_runner.observers.get_observer", lambda _name: _Observer())
    job = _job(
        tmp_path,
        stage="execute",
        files={"requirements.txt": b"demo==1.0\n"},
        wheelhouse_manifest=manifest,
        observers=["authorization_canary"],
    )
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "ok", result.error
    assert result.observer_events[0].kind == "canary_seen"
    assert result.observer_events[0].location == "GET /api"


@pytest.mark.parametrize(
    "secret",
    [
        "ghs_supersecretvalue1234",
        "ghp_supersecretvalue1234",
        "gho_supersecretvalue1234",
        "github_pat_11AAAAAAAAaaaaaaaaaa",
        "sbl_supersecretvalue1234",
        "AKIAIOSFODNN7EXAMPLE",
        "Bearer abcdefghijklmnop",
        "-----BEGIN PRIVATE KEY-----\nMIIEsecret\n-----END PRIVATE KEY-----",
    ],
)
def test_sanitizer_removes_each_secret_pattern(secret):
    cleaned = scrub(f"before {secret} after", [])
    assert secret not in cleaned
    assert "[REDACTED]" in cleaned


def test_sanitizer_removes_canaries_and_truncates():
    assert parse_canaries(json.dumps([CANARY]).encode()) == [CANARY]
    assert parse_canaries(f"{CANARY}\n".encode()) == [CANARY]
    cleaned = scrub(f"see {CANARY} now", [CANARY])
    assert CANARY not in cleaned
    huge = "ghs_supersecretvalue1234\n" + ("y" * (MAX_LOG_BYTES + 50))
    truncated = scrub(huge, [])
    assert "ghs_supersecretvalue1234" not in truncated
    assert len(truncated.encode("utf-8")) <= MAX_LOG_BYTES


def _is_deps(argv: list[str]) -> bool:
    return any("stage_deps" in part for part in argv)


def _write_wheel(argv: list[str]) -> None:
    for index, part in enumerate(argv):
        if part == "-v" and index + 1 < len(argv) and argv[index + 1].endswith(":/out:rw"):
            host = argv[index + 1][: -len(":/out:rw")]
            wheels = Path(host) / "wheels"
            wheels.mkdir(parents=True, exist_ok=True)
            (wheels / WHEEL_NAME).write_bytes(WHEEL_BYTES)


def test_proxy_dns_must_be_public_addresses(monkeypatch):
    from challenge_runner.sandbox.docker import SandboxUnavailable, proxy_resolv_conf

    monkeypatch.delenv("CHALLENGE_PROXY_DNS", raising=False)
    assert proxy_resolv_conf().startswith("nameserver 1.1.1.1\n")
    for bad in ("127.0.0.11", "10.0.0.2", "169.254.169.254", "dns.example", ""):
        monkeypatch.setenv("CHALLENGE_PROXY_DNS", bad)
        with pytest.raises(SandboxUnavailable):
            proxy_resolv_conf()
