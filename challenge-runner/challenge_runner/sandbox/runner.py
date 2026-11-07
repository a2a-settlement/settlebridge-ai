"""Host-side orchestration for the dependencies and execute stages."""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from challenge_runner.artifacts import ArtifactError, ArtifactStore
from challenge_runner.protocol import DIGEST_RE, ObserverEvent, RunnerJob, RunnerResult
from challenge_runner.sandbox.docker import (
    PROXY_PORT,
    SandboxUnavailable,
    base_flags,
    common_env,
    container_name,
    create_egress_network,
    create_internal_network,
    create_tmpfs_volume,
    egress_network_name,
    ensure_runtime,
    fallback_image_digest,
    inspect_image_digest,
    inspect_state,
    network_name,
    output_hit_disk_limit,
    package_mounts,
    pack_tree,
    proxy_container_name,
    proxy_resolv_conf,
    read_proxy_ip,
    require_image,
    require_runtime,
    run_detached,
    MAX_EXPORT_BYTES,
    run_foreground,
    teardown,
    volume_mount,
    volume_name,
    wrap_command,
)
from challenge_runner.sandbox.sanitize import parse_canaries, scrub
from challenge_runner.sandbox.tarutil import DiskLimitExceeded, UnsafeArchive, safe_extract

_WHEEL_NAME = re.compile(r"^[A-Za-z0-9._+-]+$")
_HOST_NAME = re.compile(r"^[A-Za-z0-9.-]{1,253}$")
_HASH = re.compile(r"--hash(?:=|\s+)sha256:[0-9a-f]{64}")
# Flags that take a value. They are not forwarded: the sandbox picks the index.
_DROPPED_VALUE_FLAGS = {
    "-i",
    "--index-url",
    "--extra-index-url",
    "--trusted-host",
    "-f",
    "--find-links",
}


@dataclass
class _Outcome:
    status: str
    error: str | None = None
    exit_code: int | None = None
    cpu_seconds: int = 0
    wall_seconds: int = 0
    wheelhouse_manifest: str | None = None
    events: tuple[ObserverEvent, ...] = ()
    log_text: str = ""
    captured: bool = False
    archive: bytes = b""


@dataclass
class _Resources:
    container: str
    proxy: str
    network: str
    egress_network: str = ""
    started_container: bool = False
    started_proxy: bool = False
    started_network: bool = False
    started_egress: bool = False
    volumes: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def unavailable_result(job: RunnerJob, message: str) -> RunnerResult:
    error = scrub(message, [])[:500] or "sandbox runtime unavailable"
    return RunnerResult(
        execution_id=job.execution_id,
        stage=job.stage,
        status="sandbox_error",
        image_digest=fallback_image_digest(),
        source_digest=job.source_tarball,
        error=error,
    )


def run_job(job: RunnerJob, *, artifact_root: Path) -> RunnerResult:
    store = ArtifactStore(Path(artifact_root))
    try:
        image = require_image()
        runtime = require_runtime()
        ensure_runtime(runtime)
        image_digest = inspect_image_digest(image)
    except SandboxUnavailable as exc:
        return unavailable_result(job, str(exc))

    resources = _Resources(
        container=container_name(job.execution_id),
        proxy=proxy_container_name(job.execution_id),
        network=network_name(job.execution_id),
        egress_network=egress_network_name(job.execution_id),
    )
    outcome: _Outcome | None = None
    tmp = Path(tempfile.mkdtemp(prefix="sbx-"))
    try:
        try:
            outcome = _run_stage(job, store, tmp, image, runtime, resources)
        except SandboxUnavailable as exc:
            outcome = _Outcome("sandbox_error", error=str(exc))
        except DiskLimitExceeded:
            outcome = _Outcome("limit_exceeded", error="disk limit exceeded")
        except UnsafeArchive as exc:
            outcome = _Outcome("sandbox_error", error=str(exc))
        except Exception as exc:
            outcome = _Outcome(
                "sandbox_error",
                error="sandbox error",
                log_text=f"{type(exc).__name__}: {exc}",
            )
    finally:
        resources.notes = teardown(
            container=resources.container if resources.started_container else None,
            proxy=resources.proxy if resources.started_proxy else None,
            network=resources.network if resources.started_network else None,
            egress_network=resources.egress_network if resources.started_egress else None,
            volumes=resources.volumes,
        )
        shutil.rmtree(tmp, ignore_errors=True)
    if outcome is None:
        outcome = _Outcome("sandbox_error", error="sandbox error")
    return _finish(job, store, outcome, image_digest, resources.notes)


def _run_stage(
    job: RunnerJob,
    store: ArtifactStore,
    tmp: Path,
    image: str,
    runtime: str,
    resources: _Resources,
) -> _Outcome:
    if job.stage not in ("dependencies", "execute"):
        return _Outcome("sandbox_error", error="stage")
    max_bytes = job.limits.disk_mb * 1024 * 1024
    try:
        source_archive = store.get_path(job.source_tarball)
    except (ArtifactError, FileNotFoundError):
        return _Outcome("sandbox_error", error="source tarball unavailable")
    if job.stage == "dependencies":
        return _dependencies(job, store, tmp, image, runtime, resources, source_archive, max_bytes)
    return _execute(job, store, tmp, image, runtime, resources, source_archive, max_bytes)


def _dependencies(
    job: RunnerJob,
    store: ArtifactStore,
    tmp: Path,
    image: str,
    runtime: str,
    resources: _Resources,
    source_archive: Path,
    max_bytes: int,
) -> _Outcome:
    src = tmp / "src"
    out = tmp / "out"
    src.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    safe_extract(source_archive, src, max_bytes=max_bytes)
    _relax(src, write=False)
    requirements, packages, require_hashes = _requirements(src, job.install_command, "/src")
    _check_allowlist(job.allowlist_hosts)
    work_volume = _ensure_volume(resources, job, "work")
    out_volume = _ensure_volume(resources, job, "out")
    create_internal_network(resources.network)
    resources.started_network = True
    create_egress_network(resources.egress_network)
    resources.started_egress = True
    resolv = tmp / "resolv.conf"
    resolv.write_text(proxy_resolv_conf(), encoding="utf-8")
    resolv.chmod(0o444)
    proxy_argv = _proxy_argv(job, image, runtime, resources, work_volume, resolv)
    resources.started_proxy = True
    started = run_detached(proxy_argv)
    if started.returncode != 0:
        return _Outcome(
            "sandbox_error",
            error="sandbox runtime unavailable",
            log_text=(started.stderr or "") + (started.stdout or ""),
            captured=True,
        )
    ip = read_proxy_ip(resources.proxy, resources.network)
    proxy_url = f"http://{ip}:{PROXY_PORT}"
    command = _deps_command(requirements, packages, require_hashes)
    argv = [
        "docker",
        "run",
        *base_flags(job, name=resources.container, runtime=runtime),
        f"--network={resources.network}",
        *package_mounts(work_volume),
        "-v",
        f"{src}:/src:ro",
        *volume_mount(out_volume, "/out"),
        *common_env(
            extra=(
                ("HTTPS_PROXY", proxy_url),
                ("HTTP_PROXY", proxy_url),
                ("https_proxy", proxy_url),
                ("http_proxy", proxy_url),
                ("TMPDIR", "/out/tmp"),
            )
        ),
        image,
        *wrap_command(command, seed=None, export="/out"),
    ]
    outcome = _run_container(job, resources, argv)
    _apply_disk_limit(outcome)
    if outcome.status == "ok" and _extract_archive(outcome, out, max_bytes):
        outcome.wheelhouse_manifest = _publish_wheelhouse(store, out / "wheels")
    return outcome


def _execute(
    job: RunnerJob,
    store: ArtifactStore,
    tmp: Path,
    image: str,
    runtime: str,
    resources: _Resources,
    source_archive: Path,
    max_bytes: int,
) -> _Outcome:
    if not job.wheelhouse_manifest:
        return _Outcome("sandbox_error", error="wheelhouse_manifest required")
    if not job.test_command or not all(isinstance(part, str) and part for part in job.test_command):
        return _Outcome("sandbox_error", error="test_command required")
    work = tmp / "work"
    wheels = tmp / "wheels"
    verifier = tmp / "verifier"
    capture = tmp / "capture"
    for path in (work, wheels, verifier, capture):
        path.mkdir(parents=True, exist_ok=True)
    safe_extract(source_archive, work, max_bytes=max_bytes)
    if job.challenger_bundle:
        try:
            bundle = store.get_path(job.challenger_bundle)
        except (ArtifactError, FileNotFoundError):
            return _Outcome("sandbox_error", error="challenger bundle unavailable")
        safe_extract(
            bundle,
            work,
            required_prefix="challenge_tests",
            max_bytes=max_bytes,
        )
    if job.fixtures_digest:
        try:
            fixtures = store.get_path(job.fixtures_digest)
        except (ArtifactError, FileNotFoundError):
            return _Outcome("sandbox_error", error="fixtures unavailable")
        safe_extract(fixtures, verifier, max_bytes=max_bytes)
    _install_observers(verifier)
    try:
        _materialize_wheelhouse(store, job.wheelhouse_manifest, wheels)
    except (ArtifactError, FileNotFoundError, json.JSONDecodeError, OSError):
        return _Outcome("sandbox_error", error="wheelhouse unavailable")
    (verifier / "capture").mkdir(parents=True, exist_ok=True)
    _relax(work, write=True)
    _relax(wheels, write=False)
    _relax(verifier, write=False)
    _relax(capture, write=True)
    requirements, packages, _hashes = _requirements(work, job.install_command, "/work")
    command = _exec_command(requirements, packages, job.test_command)
    work_volume = _ensure_volume(resources, job, "work")
    capture_volume = _ensure_volume(resources, job, "capture")
    argv = [
        "docker",
        "run",
        "-i",
        *base_flags(job, name=resources.container, runtime=runtime),
        "--network=none",
        *package_mounts(work_volume),
        "-v",
        f"{wheels}:/wheels:ro",
        "-v",
        f"{verifier}:/verifier:ro",
        *volume_mount(capture_volume, "/verifier/capture"),
        *common_env(extra=(("TMPDIR", "/work/tmp"),)),
        image,
        *wrap_command(command, seed="/work", export="/verifier/capture"),
    ]
    outcome = _run_container(job, resources, argv, stdin=pack_tree(work))
    _apply_disk_limit(outcome)
    if outcome.status in ("ok", "test_failed"):
        _extract_archive(outcome, capture, max_bytes)
    if outcome.status in ("ok", "test_failed") and job.observers:
        try:
            outcome.events = tuple(_collect_observers(job, capture))
        except NotImplementedError:
            outcome.status = "sandbox_error"
            outcome.error = "observer unavailable"
            outcome.events = ()
        except Exception:
            outcome.status = "sandbox_error"
            outcome.error = "observer failed"
            outcome.events = ()
    return outcome


def _run_container(
    job: RunnerJob,
    resources: _Resources,
    argv: list[str],
    stdin: bytes | None = None,
) -> _Outcome:
    resources.started_container = True
    started = time.monotonic()
    archive_cap = min(job.limits.disk_mb * 1024 * 1024, MAX_EXPORT_BYTES)
    code, stdout, stderr, timed_out = run_foreground(
        argv,
        timeout=min(job.limits.wall_seconds, job.limits.cpu_seconds),
        kill_name=resources.container,
        stdin=stdin,
        max_archive_bytes=archive_cap,
    )
    wall = max(0, int(time.monotonic() - started))
    oom, inspected = inspect_state(resources.container)
    exit_code = inspected if inspected is not None else code
    if timed_out and exit_code is None:
        exit_code = 137
    status = _classify(exit_code, timed_out, oom)
    error = None
    if status == "limit_exceeded":
        error = "disk limit exceeded" if len(stdout) >= archive_cap else "limit exceeded"
    elif status == "sandbox_error":
        error = "docker run failed"
    return _Outcome(
        status,
        error=error,
        exit_code=exit_code,
        # --cpus=1 bounds CPU time by wall time, so wall is the CPU upper bound.
        cpu_seconds=wall,
        wall_seconds=wall,
        log_text=stderr.decode("utf-8", "replace"),
        captured=True,
        archive=stdout,
    )


def _classify(exit_code: int | None, timed_out: bool, oom: bool) -> str:
    if timed_out or oom or exit_code == 137:
        return "limit_exceeded"
    if exit_code == 125 or exit_code is None:
        return "sandbox_error"
    if exit_code == 0:
        return "ok"
    return "test_failed"


def _proxy_argv(
    job: RunnerJob,
    image: str,
    runtime: str,
    resources: _Resources,
    work_volume: str,
    resolv: Path,
) -> list[str]:
    allow = ",".join(job.allowlist_hosts)
    # Both networks at create time: gVisor does not see networks connected later.
    return [
        "docker",
        "run",
        "-d",
        *base_flags(job, name=resources.proxy, runtime=runtime),
        f"--network={resources.egress_network}",
        f"--network={resources.network}",
        "-v",
        f"{resolv}:/etc/resolv.conf:ro",
        *package_mounts(work_volume),
        *common_env(extra=(("TMPDIR", "/tmp"),)),
        image,
        "python",
        "-m",
        "challenge_runner.sandbox.proxy",
        "--allow",
        allow,
        "--port",
        str(PROXY_PORT),
        "--listen",
        "0.0.0.0",
    ]


def _deps_command(requirements: list[str], packages: list[str], require_hashes: bool) -> list[str]:
    command = ["python", "-m", "challenge_runner.sandbox.stage_deps", "--dest", "/out/wheels"]
    for requirement in requirements:
        command.extend(["--requirement", requirement])
    for package in packages:
        command.extend(["--package", package])
    if require_hashes:
        command.append("--require-hashes")
    return command


def _exec_command(
    requirements: list[str], packages: list[str], test_command: tuple[str, ...]
) -> list[str]:
    command = [
        "python",
        "-m",
        "challenge_runner.sandbox.stage_exec",
        "--find-links",
        "/wheels",
        "--target",
        "/work/.pydeps",
    ]
    for requirement in requirements:
        command.extend(["--requirement", requirement])
    for package in packages:
        command.extend(["--package", package])
    if not requirements and not packages:
        command.append("--all-wheels")
    command.append("--")
    command.extend(test_command)
    return command


def _requirements(
    source: Path, install_command: tuple[str, ...], container_root: str
) -> tuple[list[str], list[str], bool]:
    files: list[Path] = []
    packages: list[str] = []
    tokens = list(install_command)
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not isinstance(token, str):
            raise UnsafeArchive("refusing unsafe path")
        if token in {"-r", "--requirement", "-c", "--constraint"} and index + 1 < len(tokens):
            files.append(_host_requirement(source, tokens[index + 1]))
            index += 2
            continue
        if token.startswith("--requirement="):
            files.append(_host_requirement(source, token.split("=", 1)[1]))
            index += 1
            continue
        if token in _DROPPED_VALUE_FLAGS and index + 1 < len(tokens):
            index += 2
            continue
        if token.startswith("-") or token in {
            "pip",
            "pip3",
            "python",
            "python3",
            "install",
            "download",
            "wheel",
            "-m",
        }:
            index += 1
            continue
        if any(char in token for char in "\n\r\x00"):
            raise UnsafeArchive("refusing unsafe path")
        packages.append(token)
        index += 1
    if not files and not packages:
        for name in ("requirements.txt", "requirements.lock", "requirements-lock.txt"):
            candidate = source / name
            if candidate.is_file():
                files.append(candidate)
                break
    container_files: list[str] = []
    require_hashes = False
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        if _HASH.search(text):
            require_hashes = True
        relative = path.resolve().relative_to(source.resolve()).as_posix()
        container_files.append(f"{container_root}/{relative}")
    return container_files, packages, require_hashes


def _host_requirement(source: Path, raw: str) -> Path:
    if not isinstance(raw, str) or not raw or raw.startswith("/") or "\\" in raw:
        raise UnsafeArchive("requirement file missing")
    if ".." in Path(raw).parts:
        raise UnsafeArchive("refusing parent path")
    path = (source / raw).resolve()
    if not path.is_relative_to(source.resolve()) or not path.is_file():
        raise UnsafeArchive("requirement file missing")
    return path


def _check_allowlist(hosts: tuple[str, ...]) -> None:
    for host in hosts:
        if _HOST_NAME.fullmatch(host) is None or ".." in host:
            raise UnsafeArchive("refusing unsafe path")


def _publish_wheelhouse(store: ArtifactStore, wheels: Path) -> str:
    manifest: dict[str, str] = {}
    if wheels.is_dir():
        for path in sorted(wheels.iterdir(), key=lambda item: item.name):
            if not path.is_file() or path.is_symlink():
                continue
            if _WHEEL_NAME.fullmatch(path.name) is None:
                raise UnsafeArchive("refusing unsafe path")
            manifest[path.name] = store.put_file(path)
    payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    return store.put_bytes(payload)


def _materialize_wheelhouse(store: ArtifactStore, digest: str, dest: Path) -> None:
    if DIGEST_RE.fullmatch(digest) is None:
        raise ArtifactError("refusing digest")
    manifest = json.loads(store.get_path(digest).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ArtifactError("wheelhouse unavailable")
    for name, file_digest in manifest.items():
        if not isinstance(name, str) or _WHEEL_NAME.fullmatch(name) is None:
            raise ArtifactError("refusing wheel name")
        if not isinstance(file_digest, str):
            raise ArtifactError("refusing digest")
        source = store.get_path(file_digest)
        target = dest / name
        shutil.copyfile(source, target)
        os.chmod(target, 0o644)


def _install_observers(verifier: Path) -> None:
    source = Path(__file__).resolve().parents[1] / "observers"
    dest = verifier / "observers"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(source, dest, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def _collect_observers(job: RunnerJob, capture: Path) -> list[ObserverEvent]:
    from challenge_runner.observers import get_observer

    events: list[ObserverEvent] = []
    for name in job.observers:
        observer = get_observer(name)
        for event in observer.observe(str(capture)):
            if not isinstance(event, ObserverEvent):
                raise TypeError("observer event")
            events.append(event)
    return events


def _finish(
    job: RunnerJob,
    store: ArtifactStore,
    outcome: _Outcome,
    image_digest: str,
    notes: list[str],
) -> RunnerResult:
    try:
        canaries = _load_canaries(store, job.canaries_digest)
    except (ArtifactError, FileNotFoundError, ValueError, json.JSONDecodeError, OSError):
        return RunnerResult(
            execution_id=job.execution_id,
            stage=job.stage,
            status="sandbox_error",
            image_digest=image_digest,
            source_digest=job.source_tarball,
            error="canaries unavailable",
        )
    text = outcome.log_text
    if notes:
        extra = "\n".join(f"[teardown] {note}" for note in notes)
        text = f"{text}\n{extra}" if text else extra
    scrubbed = scrub(text, canaries)
    error = scrub(outcome.error, canaries)[:500] if outcome.error else None
    events = tuple(
        ObserverEvent(
            observer=event.observer,
            kind=event.kind,
            detail=scrub(event.detail, canaries),
            location=scrub(event.location, canaries),
        )
        for event in outcome.events
    )
    log_digest = None
    if outcome.captured or notes or scrubbed:
        log_digest = store.put_bytes(scrubbed.encode("utf-8"))
    return RunnerResult(
        execution_id=job.execution_id,
        stage=job.stage,
        status=outcome.status,
        image_digest=image_digest,
        source_digest=job.source_tarball,
        exit_code=outcome.exit_code,
        cpu_seconds=outcome.cpu_seconds,
        wall_seconds=outcome.wall_seconds,
        wheelhouse_manifest=outcome.wheelhouse_manifest,
        observer_events=events,
        log_digest=log_digest,
        error=error,
    )


def _load_canaries(store: ArtifactStore, digest: str | None) -> list[str]:
    if not digest:
        return []
    return parse_canaries(store.get_path(digest).read_bytes())


def _ensure_volume(resources: _Resources, job: RunnerJob, role: str) -> str:
    name = volume_name(job.execution_id, role)
    create_tmpfs_volume(name, job.limits.disk_mb)
    resources.volumes.append(name)
    return name


def _extract_archive(outcome: _Outcome, dest: Path, max_bytes: int) -> bool:
    """Unpack the job's stdout archive. Returns False when ``outcome`` changed."""
    raw = dest.parent / f"{dest.name}.raw.tar"
    cleaned = dest.parent / f"{dest.name}.clean.tar"
    try:
        raw.write_bytes(outcome.archive)
        _drop_root_members(raw, cleaned)
        safe_extract(cleaned, dest, max_bytes=max_bytes)
    except DiskLimitExceeded:
        outcome.status = "limit_exceeded"
        outcome.error = "disk limit exceeded"
        return False
    except (UnsafeArchive, tarfile.TarError, OSError) as exc:
        outcome.status = "sandbox_error"
        outcome.error = str(exc) or "sandbox runtime unavailable"
        return False
    return True


def _drop_root_members(source: Path, dest: Path) -> None:
    """Drop the ``.`` member GNU tar writes for ``tar -c -C dir .``."""
    with tarfile.open(source, "r|*") as incoming, tarfile.open(dest, "w|") as outgoing:
        for member in incoming:
            payload = incoming.extractfile(member) if member.isreg() else None
            if _empty_member(member.name):
                if payload is not None:
                    payload.read()
                continue
            outgoing.addfile(member, payload)


def _empty_member(name: str) -> bool:
    parts = [part for part in PurePosixPath(name).parts if part not in (".",)]
    return not parts


def _apply_disk_limit(outcome: _Outcome) -> None:
    if outcome.status in ("ok", "test_failed") and output_hit_disk_limit(outcome.log_text):
        outcome.status = "limit_exceeded"
        outcome.error = "disk limit exceeded"


def _relax(root: Path, *, write: bool) -> None:
    dir_mode = 0o777 if write else 0o755
    file_mode = 0o666 if write else 0o644
    os.chmod(root, dir_mode)
    for dirpath, dirnames, filenames in os.walk(root):
        for dirname in dirnames:
            os.chmod(Path(dirpath) / dirname, dir_mode)
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.is_symlink():
                continue
            os.chmod(path, file_mode)
