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


class _StdinSink:
    def __init__(self, fake: "FakeDocker"):
        self.fake = fake
        self.parts: list[bytes] = []

    def write(self, data: bytes) -> int:
        self.parts.append(data)
        return len(data)

    def close(self) -> None:
        if self.parts:
            self.fake.job_stdin.append(b"".join(self.parts))


class FakeProc:
    def __init__(self, fake: "FakeDocker", argv: list[str]):
        self.fake = fake
        self.argv = argv
        self.returncode = None
        self._calls = 0
        self.stdin = _StdinSink(fake)
        self.stdout = io.BytesIO(self._archive())
        self.stderr = io.BytesIO(_as_bytes(fake.stderr))

    def wait(self, timeout=None):
        self._calls += 1
        if self.fake.mode == "explode":
            raise RuntimeError("docker exploded")
        if self.fake.mode == "timeout" and self._calls == 1 and timeout is not None:
            raise __import__("subprocess").TimeoutExpired(self.argv, timeout)
        self.returncode = 137 if self.fake.mode == "oom" else self.fake.exit_code
        return self.returncode

    def kill(self):
        self.returncode = -9

    def _archive(self) -> bytes:
        stage = any("stage_deps" in part or "stage_exec" in part for part in self.argv)
        if not stage:
            return b""
        if self.fake.out_tar is not None:
            return self.fake.out_tar
        if self.fake.mode == "ok" and any("stage_deps" in part for part in self.argv):
            return _wheel_tar_bytes()
        return _empty_tar_bytes()


class FakeDocker:
    def __init__(self):
        self.calls: list[list[str]] = []
        self.mode = "ok"
        self.exit_code = 0
        self.stderr = f"log {SECRET} {CANARY}\n"
        self.rm_fail = False
        self.proxy_rc = 0
        self.out_tar: bytes | None = None
        self.job_stdin: list[bytes] = []
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
        if argv[1:3] == ["volume", "create"]:
            return subprocess.CompletedProcess(argv, 0, argv[-1] + "\n", "")
        if argv[1:3] == ["volume", "rm"]:
            code = 1 if self.rm_fail else 0
            err = "rm failed" if self.rm_fail else ""
            return subprocess.CompletedProcess(argv, code, "", err)
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
            err = "proxy failed" if self.proxy_rc else ""
            return subprocess.CompletedProcess(argv, self.proxy_rc, "proxy\n", err)
        raise AssertionError(f"unexpected docker argv: {argv}")

    def popen(self, argv, shell=False, stdout=None, stderr=None, stdin=None, text=None, **kwargs):
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
    root: Path,
    *,
    stage: str,
    files: dict[str, bytes],
    execution_id: str = "exec-0001ab",
    disk_mb: int = 1024,
    **extra,
):
    source = _store(root, _tar(files))
    limits = {
        "cpu_seconds": 30,
        "memory_mb": 512,
        "wall_seconds": 30,
        "disk_mb": disk_mb,
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


def test_runc_is_refused_before_any_docker_call(fake, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CHALLENGE_SANDBOX_RUNTIME", "runc")
    job = _job(tmp_path, stage="dependencies", files={"requirements.txt": b"requests==1\n"})
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "sandbox_error"
    assert "runtime" in (result.error or "")
    assert not _runs(fake, "stage_deps")


def test_runtime_named_runsc_that_points_at_runc_is_refused(fake, tmp_path: Path):
    fake.runtimes = {"runsc": {"path": "/usr/bin/runc"}}
    job = _job(tmp_path, stage="dependencies", files={"requirements.txt": b"requests==1\n"})
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "sandbox_error"
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
    work_vol = "sbx-vol-exec-0001abcd-work"
    out_vol = "sbx-vol-exec-0001abcd-out"
    assert _volume_create(work_vol) in fake.calls
    assert _volume_create(out_vol) in fake.calls
    assert f"type=volume,src={work_vol},dst=/work" in argv
    assert f"type=volume,src={out_vol},dst=/out" in argv
    assert f"type=volume,src={work_vol},dst=/work" in proxy[0]
    assert f"type=volume,src={out_vol},dst=/out" not in proxy[0]
    _assert_no_rw_binds(fake.calls)
    _assert_volume_wrapper(argv, export="/out", seed=None)
    assert "-i" not in argv
    assert fake.job_stdin == []
    assert not _helper_names(fake.calls)
    assert fake.calls[-6:] == [
        ["docker", "rm", "-f", "sbx-exec-0001abcd"],
        ["docker", "rm", "-f", "sbx-proxy-exec-0001abcd"],
        ["docker", "volume", "rm", work_vol],
        ["docker", "volume", "rm", out_vol],
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
    assert "type=volume,src=sbx-vol-exec-0001ab-capture,dst=/verifier/capture" in argv
    assert "type=volume,src=sbx-vol-exec-0001ab-work,dst=/work" in argv
    assert ":/verifier/capture:rw" not in joined
    assert "pytest" in argv
    assert "challenge_tests" in argv
    assert ["docker", "network", "create", "--internal", "sbx-net-exec-0001ab"] not in fake.calls
    assert _volume_create("sbx-vol-exec-0001ab-work") in fake.calls
    assert _volume_create("sbx-vol-exec-0001ab-capture") in fake.calls
    _assert_no_rw_binds(fake.calls)
    assert "-i" in argv
    _assert_volume_wrapper(argv, export="/verifier/capture", seed="/work")
    assert any(b"ustar" in blob[:512] and b"requirements.txt" in blob for blob in fake.job_stdin)
    assert not _helper_names(fake.calls)
    runs = [call for call in fake.calls if len(call) > 1 and call[1] == "run"]
    assert runs == [argv]
    assert fake.calls[-3:] == [
        ["docker", "rm", "-f", "sbx-exec-0001ab"],
        ["docker", "volume", "rm", "sbx-vol-exec-0001ab-work"],
        ["docker", "volume", "rm", "sbx-vol-exec-0001ab-capture"],
    ]
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
    assert ["docker", "volume", "rm", "sbx-vol-exec-tear0001-work"] in fake.calls
    assert ["docker", "volume", "rm", "sbx-vol-exec-tear0001-out"] in fake.calls
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
    assert ["docker", "volume", "rm", "sbx-vol-exec-0001ab-work"] in fake.calls
    assert ["docker", "volume", "rm", "sbx-vol-exec-0001ab-out"] in fake.calls

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
    assert not any(call[1:3] == ["volume", "create"] for call in fake.calls)


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


def _volume_create(name: str, disk_mb: int = 1024) -> list[str]:
    return [
        "docker",
        "volume",
        "create",
        "--driver",
        "local",
        "--opt",
        "type=tmpfs",
        "--opt",
        "device=tmpfs",
        "--opt",
        f"o=size={disk_mb}m,uid=65534,gid=65534,mode=0700",
        name,
    ]


def _assert_no_rw_binds(calls: list[list[str]]) -> None:
    for argv in calls:
        for part in argv:
            assert not part.endswith(":/out:rw")
            assert not part.endswith(":/work:rw")
            assert not part.endswith(":/verifier/capture:rw")


def _assert_volume_wrapper(argv: list[str], *, export: str, seed: str | None) -> None:
    flag = argv.index("-c")
    assert argv[flag - 1] == "python"
    script = argv[flag + 1]
    assert "tar" in script and "--no-same-owner" in script
    rest = argv[flag + 2 :]
    if seed is None:
        assert "--seed" not in rest
    else:
        assert rest[:2] == ["--seed", seed]
        rest = rest[2:]
    assert rest[:3] == ["--export", export, "--"]


def _helper_names(calls: list[list[str]]) -> list[str]:
    return [part for argv in calls for part in argv if part.startswith("sbx-h-")]


def _as_bytes(value: str | bytes) -> bytes:
    return value if isinstance(value, bytes) else value.encode()


def _wheel_tar_bytes() -> bytes:
    return _tar_bytes(
        {
            "./": b"",
            "./wheels": b"",
            f"./wheels/{WHEEL_NAME}": WHEEL_BYTES,
        }
    )


def _empty_tar_bytes() -> bytes:
    return _tar_bytes({"./": b""})


def _tar_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            if name.endswith("/") or data == b"" and name in {"./", "./wheels"}:
                info.type = tarfile.DIRTYPE
                info.size = 0
                archive.addfile(info)
                continue
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def test_enospc_in_execute_maps_to_disk_limit_and_removes_volumes(fake, tmp_path: Path):
    fake.mode = "fail"
    fake.exit_code = 1
    fake.stderr = "OSError: [Errno 28] No space left on device\n"
    fake.out_tar = b"not-a-tar"
    wheel = _store(tmp_path, WHEEL_BYTES)
    manifest = _store(tmp_path, json.dumps({WHEEL_NAME: wheel}).encode())
    job = _job(
        tmp_path,
        stage="execute",
        files={"probe.py": b"print('x')\n"},
        wheelhouse_manifest=manifest,
        disk_mb=8,
        install_command=[],
    )
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "limit_exceeded"
    assert result.error == "disk limit exceeded"
    work_vol = "sbx-vol-exec-0001ab-work"
    capture_vol = "sbx-vol-exec-0001ab-capture"
    assert _volume_create(work_vol, 8) in fake.calls
    assert _volume_create(capture_vol, 8) in fake.calls
    argv = _runs(fake, "stage_exec")[0]
    assert f"type=volume,src={work_vol},dst=/work" in argv
    assert f"type=volume,src={capture_vol},dst=/verifier/capture" in argv
    create_at = fake.calls.index(_volume_create(work_vol, 8))
    run_at = fake.calls.index(argv)
    remove_at = fake.calls.index(["docker", "volume", "rm", work_vol])
    assert create_at < run_at < remove_at
    assert ["docker", "volume", "rm", capture_vol] in fake.calls
    assert not _helper_names(fake.calls)
    _assert_volume_wrapper(argv, export="/verifier/capture", seed="/work")
    assert any(b"probe.py" in blob for blob in fake.job_stdin)


def test_copy_out_over_disk_mb_is_disk_limit(fake, tmp_path: Path):
    payload = b"x" * (1024 * 1024 + 8)
    fake.out_tar = _tar_bytes({"./wheels/huge.bin": payload})
    job = _job(
        tmp_path,
        stage="dependencies",
        files={"requirements.txt": b"requests==2.31.0\n"},
        disk_mb=1,
    )
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "limit_exceeded", result.error
    assert result.error == "disk limit exceeded"
    assert result.wheelhouse_manifest is None
    assert ["docker", "volume", "rm", "sbx-vol-exec-0001ab-work"] in fake.calls
    assert ["docker", "volume", "rm", "sbx-vol-exec-0001ab-out"] in fake.calls


def test_volumes_removed_when_proxy_fails_before_the_job_container(fake, tmp_path: Path):
    fake.proxy_rc = 1
    job = _job(tmp_path, stage="dependencies", files={"requirements.txt": b"requests==2.31.0\n"})
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "sandbox_error"
    assert ["docker", "rm", "-f", "sbx-exec-0001ab"] not in fake.calls
    assert ["docker", "rm", "-f", "sbx-proxy-exec-0001ab"] in fake.calls
    assert ["docker", "volume", "rm", "sbx-vol-exec-0001ab-work"] in fake.calls
    assert ["docker", "volume", "rm", "sbx-vol-exec-0001ab-out"] in fake.calls
    assert ["docker", "network", "rm", "sbx-net-exec-0001ab"] in fake.calls


def test_proxy_dns_must_be_public_addresses(monkeypatch):
    from challenge_runner.sandbox.docker import SandboxUnavailable, proxy_resolv_conf

    monkeypatch.delenv("CHALLENGE_PROXY_DNS", raising=False)
    assert proxy_resolv_conf().startswith("nameserver 1.1.1.1\n")
    for bad in ("127.0.0.11", "10.0.0.2", "169.254.169.254", "dns.example", ""):
        monkeypatch.setenv("CHALLENGE_PROXY_DNS", bad)
        with pytest.raises(SandboxUnavailable):
            proxy_resolv_conf()


def test_export_past_the_disk_cap_kills_the_container(fake, tmp_path: Path):
    fake.out_tar = b"x" * (2 * 1024 * 1024)
    job = _job(tmp_path, stage="dependencies", files={"requirements.txt": b"demo\n"}, disk_mb=1)
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "limit_exceeded"
    assert result.error == "disk limit exceeded"
    assert ["docker", "kill", "sbx-exec-0001ab"] in fake.calls


def test_host_keeps_a_bounded_slice_of_an_unbounded_log(fake, tmp_path: Path):
    fake.stderr = "z" * (MAX_LOG_BYTES * 3)
    job = _job(tmp_path, stage="dependencies", files={"requirements.txt": b"demo\n"})
    result = run_job(job, artifact_root=tmp_path)
    assert result.status == "ok", result.error
    log = ArtifactStore(tmp_path).get_path(result.log_digest).read_bytes()
    assert len(log) <= MAX_LOG_BYTES + 4096
