"""Docker argv construction and process control for the gVisor sandbox.

The runner never invokes a shell. Image and runtime come from the environment.
``--rm`` is intentionally absent; teardown removes containers explicitly.
"""

from __future__ import annotations

import hashlib
import io
import ipaddress
import json
import os
import re
import subprocess
import tarfile
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from challenge_runner.protocol import DIGEST_RE, RunnerJob
from challenge_runner.sandbox.sanitize import MAX_LOG_BYTES

PINNED_IMAGE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}"
    r"(?::[A-Za-z0-9._-]{1,128})?"
    r"@sha256:[0-9a-f]{64}$"
)
RUNTIME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,32}$")
# Scratch only. disk_mb caps the writable volumes; /tmp stays noexec so pip
# cannot run build scripts there.
TMPFS_SPEC = "/tmp:rw,noexec,nosuid,size=256m"
INFO_FORMAT = "{{json .Runtimes}}"
IMAGE_DIGEST_FORMAT = "{{index .RepoDigests 0}}"
NETWORK_FORMAT = "{{json .NetworkSettings.Networks}}"
STATE_FORMAT = "{{.State.OOMKilled}}|{{.State.ExitCode}}"
PROXY_PORT = 8080
PIDS_OVERHEAD = 256
# The export is buffered in host memory, so it has a ceiling below any disk_mb.
MAX_EXPORT_BYTES = 1024 * 1024 * 1024
_FALLBACK_IMAGE = "sha256:" + hashlib.sha256(b"sandbox-unavailable").hexdigest()


class SandboxUnavailable(Exception):
    """Docker or the runsc runtime is missing; the caller reports sandbox_error."""


_DISK_LIMIT_RE = re.compile(r"no space left on device|errno 28\b|enospc", re.IGNORECASE)
_VOLUME_ROLES = {"work", "out", "capture"}
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")
# runsc gives each container a private copy of a tmpfs volume, so this process
# extracts stdin before the job and packs the export to stdout afterward.
_VOLUME_WRAPPER = "\n".join(
    [
        "import subprocess, sys",
        "args = sys.argv[1:]",
        "seed = export = None",
        "job = []",
        "index = 0",
        "while index < len(args):",
        "    item = args[index]",
        "    if item == '--':",
        "        job = args[index + 1:]",
        "        break",
        "    if item == '--seed' and index + 1 < len(args):",
        "        seed = args[index + 1]",
        "        index += 2",
        "        continue",
        "    if item == '--export' and index + 1 < len(args):",
        "        export = args[index + 1]",
        "        index += 2",
        "        continue",
        "    index += 1",
        "code = 0",
        "if seed:",
        "    code = subprocess.run(",
        "        ['tar', '--no-same-owner', '-x', '-C', seed],",
        "        stdin=sys.stdin.buffer,",
        "    ).returncode",
        "if code == 0 and job:",
        "    code = subprocess.run(",
        "        job,",
        "        stdin=subprocess.DEVNULL,",
        "        stdout=sys.stderr.buffer,",
        "        stderr=subprocess.STDOUT,",
        "    ).returncode",
        "if export:",
        "    packed = subprocess.run(",
        "        ['tar', '-c', '-C', export, '.'],",
        "        stdin=subprocess.DEVNULL,",
        "        stdout=sys.stdout.buffer,",
        "        stderr=sys.stderr.buffer,",
        "    ).returncode",
        "    if code == 0:",
        "        code = packed",
        "sys.exit(code or 0)",
    ]
)


def fallback_image_digest() -> str:
    image = os.environ.get("CHALLENGE_SANDBOX_IMAGE", "")
    if PINNED_IMAGE_RE.fullmatch(image):
        match = re.search(r"sha256:[0-9a-f]{64}", image)
        if match and DIGEST_RE.fullmatch(match.group(0)):
            return match.group(0)
    return _FALLBACK_IMAGE


def require_image() -> str:
    image = os.environ.get("CHALLENGE_SANDBOX_IMAGE", "")
    if PINNED_IMAGE_RE.fullmatch(image) is None:
        raise SandboxUnavailable("sandbox image is not pinned to sha256")
    return image


def require_runtime() -> str:
    runtime = os.environ.get("CHALLENGE_SANDBOX_RUNTIME", "runsc")
    if RUNTIME_RE.fullmatch(runtime) is None:
        raise SandboxUnavailable("sandbox runtime unavailable")
    return runtime


def ensure_runtime(runtime: str) -> None:
    completed = invoke(["docker", "info", "--format", INFO_FORMAT], timeout=30)
    if completed.returncode != 0:
        raise SandboxUnavailable("sandbox runtime unavailable")
    try:
        data = json.loads(completed.stdout or "")
    except json.JSONDecodeError as exc:
        raise SandboxUnavailable("sandbox runtime unavailable") from exc
    if not isinstance(data, dict) or runtime not in data:
        raise SandboxUnavailable("sandbox runtime unavailable")


def inspect_image_digest(image: str) -> str:
    completed = invoke(
        ["docker", "image", "inspect", "--format", IMAGE_DIGEST_FORMAT, image],
        timeout=30,
    )
    if completed.returncode != 0:
        raise SandboxUnavailable("image digest unavailable")
    return normalize_image_digest(completed.stdout or "")


def normalize_image_digest(raw: str) -> str:
    match = re.search(r"sha256:[0-9a-f]{64}", raw.strip())
    if match is None or DIGEST_RE.fullmatch(match.group(0)) is None:
        raise SandboxUnavailable("image digest unavailable")
    return match.group(0)


def sanitize_execution_id(execution_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", execution_id).strip(".-")
    if not cleaned:
        cleaned = "job"
    if not cleaned[0].isalnum():
        cleaned = "j" + cleaned
    if len(cleaned) > 40:
        suffix = hashlib.sha256(execution_id.encode()).hexdigest()[:12]
        cleaned = cleaned[:27].rstrip(".-") + "-" + suffix
    return cleaned


def container_name(execution_id: str) -> str:
    return "sbx-" + sanitize_execution_id(execution_id)


def network_name(execution_id: str) -> str:
    return "sbx-net-" + sanitize_execution_id(execution_id)


def egress_network_name(execution_id: str) -> str:
    return "sbx-egress-" + sanitize_execution_id(execution_id)


def proxy_container_name(execution_id: str) -> str:
    return "sbx-proxy-" + sanitize_execution_id(execution_id)


def package_dir() -> str:
    return str(Path(__file__).resolve().parents[1])


def base_flags(job: RunnerJob, *, name: str, runtime: str) -> list[str]:
    memory = f"{job.limits.memory_mb}m"
    stop = str(min(10, job.limits.wall_seconds))
    return [
        "--name",
        name,
        f"--runtime={runtime}",
        "--read-only",
        "--tmpfs",
        TMPFS_SPEC,
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        # The job limit is enforced inside the sandbox. The cgroup also counts
        # gVisor's own threads, so it is only a backstop with headroom.
        "--ulimit",
        f"nproc={job.limits.max_processes}:{job.limits.max_processes}",
        f"--pids-limit={job.limits.max_processes + PIDS_OVERHEAD}",
        f"--memory={memory}",
        f"--memory-swap={memory}",
        "--cpus=1",
        "--user=65534:65534",
        "--workdir=/work",
        "--stop-timeout",
        stop,
    ]


def common_env(*, extra: Sequence[tuple[str, str]] = ()) -> list[str]:
    values = [
        ("PYTHONPATH", "/opt/sbxpkg"),
        ("HOME", "/tmp"),
        ("PYTHONDONTWRITEBYTECODE", "1"),
        ("PIP_DISABLE_PIP_VERSION_CHECK", "1"),
        ("PIP_NO_CACHE_DIR", "1"),
        *extra,
    ]
    argv: list[str] = []
    for key, value in values:
        if not key or "=" in key or "\n" in value or "\x00" in value:
            raise SandboxUnavailable("sandbox runtime unavailable")
        argv.extend(["-e", f"{key}={value}"])
    return argv


def output_hit_disk_limit(text: str) -> bool:
    return _DISK_LIMIT_RE.search(text or "") is not None


def volume_name(execution_id: str, role: str) -> str:
    _check_role(role)
    name = f"sbx-vol-{sanitize_execution_id(execution_id)}-{role}"
    _check_name(name)
    return name


def volume_mount(name: str, dest: str) -> list[str]:
    """Mount spec for ``docker run``.

    runsc does not see a mount or network attached after the container starts.
    """
    _check_name(name)
    if not dest.startswith("/") or "," in dest or "\x00" in dest:
        raise SandboxUnavailable("sandbox runtime unavailable")
    return ["--mount", f"type=volume,src={name},dst={dest}"]


def package_mounts(work_volume: str) -> list[str]:
    return [
        "-v",
        f"{package_dir()}:/opt/sbxpkg/challenge_runner:ro",
        *volume_mount(work_volume, "/work"),
    ]


def create_tmpfs_volume(name: str, disk_mb: int) -> None:
    """Create a per-mount tmpfs volume capped at ``disk_mb``.

    The local driver mounts a host tmpfs, so writes past the cap fail with
    ENOSPC inside the sandbox. Each writable mount holds about disk_mb of host
    RAM until teardown, and the exported archive is buffered for about that
    much again. Dependencies keeps work and out; execute keeps work and capture.
    runsc does not share the tmpfs with a second container, so the job process
    seeds it and packs the export itself.
    """
    _check_name(name)
    if isinstance(disk_mb, bool) or not isinstance(disk_mb, int) or disk_mb < 1:
        raise SandboxUnavailable("sandbox runtime unavailable")
    options = f"size={disk_mb}m,uid=65534,gid=65534,mode=0700"
    completed = invoke(
        [
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
            f"o={options}",
            name,
        ],
        timeout=30,
    )
    if completed.returncode != 0:
        raise SandboxUnavailable("sandbox runtime unavailable")


def pack_tree(root: Path) -> bytes:
    """Tar the contents of ``root`` for the job container's stdin."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        _add_tree(archive, root)
    return buffer.getvalue()


def wrap_command(command: Sequence[str], *, seed: str | None, export: str) -> list[str]:
    """Run ``command`` in the process that owns the tmpfs mounts.

    ``seed`` is extracted from stdin first. ``export`` is packed to stdout
    after the command, whose own output stays on stderr.
    """
    if not export.startswith("/") or "\x00" in export:
        raise SandboxUnavailable("sandbox runtime unavailable")
    if seed is not None and (not seed.startswith("/") or "\x00" in seed):
        raise SandboxUnavailable("sandbox runtime unavailable")
    argv = ["python", "-c", _VOLUME_WRAPPER]
    if seed is not None:
        argv.extend(["--seed", seed])
    argv.extend(["--export", export, "--", *command])
    return argv


def assert_argv_safe(argv: Sequence[str]) -> None:
    if any(part == "--rm" or part.startswith("--rm=") for part in argv):
        raise RuntimeError("refusing --rm")
    if "--privileged" in argv or "--network=host" in argv or "--net=host" in argv:
        raise RuntimeError("refusing host access")
    for index, part in enumerate(argv):
        if "docker.sock" in part:
            raise RuntimeError("refusing docker socket")
        if part in ("--network", "--net") and index + 1 < len(argv) and argv[index + 1] == "host":
            raise RuntimeError("refusing host network")
        if part == "-e":
            if index + 1 >= len(argv) or "=" not in argv[index + 1]:
                raise RuntimeError("refusing host env passthrough")
        if part == "--env" or part.startswith("--env-file"):
            raise RuntimeError("refusing host env passthrough")


def invoke(argv: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    assert_argv_safe(argv) if argv and argv[0] == "docker" and "run" in argv[:3] else None
    try:
        return subprocess.run(
            list(argv),
            shell=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise SandboxUnavailable("sandbox runtime unavailable") from exc
    except subprocess.TimeoutExpired as exc:
        raise SandboxUnavailable("sandbox runtime unavailable") from exc


def create_internal_network(name: str) -> None:
    completed = invoke(["docker", "network", "create", "--internal", name], timeout=30)
    if completed.returncode != 0:
        raise SandboxUnavailable("sandbox runtime unavailable")


def create_egress_network(name: str) -> None:
    completed = invoke(["docker", "network", "create", name], timeout=30)
    if completed.returncode != 0:
        raise SandboxUnavailable("sandbox runtime unavailable")


def proxy_resolv_conf() -> str:
    """resolv.conf for the proxy.

    gVisor uses its own network stack, so Docker's embedded resolver at 127.0.0.11
    (reached through host iptables) is unreachable; the proxy needs real upstreams.
    """
    raw = os.environ.get("CHALLENGE_PROXY_DNS", "1.1.1.1,8.8.8.8")
    servers: list[str] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            addr = ipaddress.ip_address(part)
        except ValueError as exc:
            raise SandboxUnavailable("proxy DNS is not an IP address") from exc
        if not addr.is_global:
            raise SandboxUnavailable("proxy DNS must be a public address")
        servers.append(str(addr))
    if not servers:
        raise SandboxUnavailable("proxy DNS is not configured")
    return "".join(f"nameserver {server}\n" for server in servers) + "options ndots:0\n"


def read_proxy_ip(proxy_name: str, network: str) -> str:
    completed = invoke(
        ["docker", "inspect", "--format", NETWORK_FORMAT, proxy_name],
        timeout=30,
    )
    if completed.returncode != 0:
        raise SandboxUnavailable("proxy address unavailable")
    try:
        data = json.loads(completed.stdout or "")
    except json.JSONDecodeError as exc:
        raise SandboxUnavailable("proxy address unavailable") from exc
    entry = data.get(network) if isinstance(data, dict) else None
    ip = entry.get("IPAddress") if isinstance(entry, dict) else ""
    if not isinstance(ip, str) or not ip:
        raise SandboxUnavailable("proxy address unavailable")
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError as exc:
        raise SandboxUnavailable("proxy address unavailable") from exc
    if (
        addr.is_loopback
        or addr.is_link_local
        or addr.is_unspecified
        or addr == ipaddress.ip_address("169.254.169.254")
    ):
        raise SandboxUnavailable("proxy address unavailable")
    return ip


def inspect_state(name: str) -> tuple[bool, int | None]:
    completed = invoke(["docker", "inspect", "--format", STATE_FORMAT, name], timeout=30)
    if completed.returncode != 0:
        return False, None
    raw = (completed.stdout or "").strip()
    oom_text, _, code_text = raw.partition("|")
    oom = oom_text.strip().lower() == "true"
    try:
        code = int(code_text.strip())
    except ValueError:
        return oom, None
    return oom, code


def run_detached(argv: Sequence[str]) -> subprocess.CompletedProcess[str]:
    assert_argv_safe(argv)
    return invoke(argv, timeout=60)


def run_foreground(
    argv: Sequence[str],
    *,
    timeout: float,
    kill_name: str,
    stdin: bytes | None = None,
    max_archive_bytes: int = MAX_EXPORT_BYTES,
) -> tuple[int | None, bytes, bytes, bool]:
    """Run a container. Wall-clock expiry kills it and reports killed.

    Stdout is the exported archive. Stderr is the job log. Both come from
    untrusted code, so the host keeps at most MAX_LOG_BYTES of log and kills
    the container once the archive passes max_archive_bytes.
    """
    assert_argv_safe(argv)
    try:
        proc = subprocess.Popen(
            list(argv),
            shell=False,
            stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        raise SandboxUnavailable("sandbox runtime unavailable") from exc
    archive = bytearray()
    log = bytearray()
    overflow = threading.Event()

    def on_overflow() -> None:
        overflow.set()
        _kill(kill_name)

    threads = [
        threading.Thread(
            target=_drain, args=(proc.stdout, archive, max_archive_bytes, on_overflow), daemon=True
        ),
        threading.Thread(target=_drain, args=(proc.stderr, log, MAX_LOG_BYTES, None), daemon=True),
    ]
    if stdin is not None:
        threads.append(threading.Thread(target=_feed, args=(proc.stdin, stdin), daemon=True))
    for thread in threads:
        thread.start()
    killed = False
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        killed = True
        _kill(kill_name)
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    for thread in threads:
        thread.join(timeout=15)
    killed = killed or overflow.is_set()
    return proc.returncode, bytes(archive), bytes(log), killed


def _drain(stream, sink: bytearray, cap: int, on_overflow) -> None:
    if stream is None:
        return
    tripped = False
    while chunk := stream.read(65536):
        room = cap - len(sink)
        if room > 0:
            sink.extend(chunk[:room])
        if len(chunk) > room and not tripped:
            tripped = True
            if on_overflow is not None:
                on_overflow()


def _feed(stream, data: bytes) -> None:
    try:
        stream.write(data)
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            stream.close()
        except (BrokenPipeError, OSError):
            pass


def _kill(name: str) -> None:
    try:
        invoke(["docker", "kill", name], timeout=20)
    except SandboxUnavailable:
        return


def teardown(
    *,
    container: str | None,
    proxy: str | None,
    network: str | None,
    egress_network: str | None = None,
    helpers: Sequence[str] | None = None,
    volumes: Sequence[str] | None = None,
) -> list[str]:
    """Remove helpers and containers, then volumes, then networks."""
    notes: list[str] = []
    for name in list(helpers or []):
        if name:
            notes.extend(_teardown_one(["docker", "rm", "-f", name]))
    for name in (container, proxy):
        if not name:
            continue
        notes.extend(_teardown_one(["docker", "rm", "-f", name]))
    for name in list(volumes or []):
        if name:
            notes.extend(_remove_volume(name))
    for net in (network, egress_network):
        if net:
            notes.extend(_teardown_one(["docker", "network", "rm", net]))
    return notes


def _check_role(role: str) -> None:
    if role not in _VOLUME_ROLES:
        raise SandboxUnavailable("sandbox runtime unavailable")


def _check_name(name: str) -> None:
    if _NAME_RE.fullmatch(name) is None or "," in name:
        raise SandboxUnavailable("sandbox runtime unavailable")


def _add_tree(archive: tarfile.TarFile, root: Path) -> None:
    root = root.resolve()
    if not root.is_dir():
        raise SandboxUnavailable("sandbox runtime unavailable")
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        current = Path(dirpath)
        relative = current.relative_to(root).as_posix()
        if relative != ".":
            archive.add(current, arcname=relative, recursive=False)
        kept: list[str] = []
        for name in dirnames:
            path = current / name
            arcname = name if relative == "." else f"{relative}/{name}"
            if path.is_symlink():
                archive.add(path, arcname=arcname, recursive=False)
            else:
                kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            path = current / name
            arcname = name if relative == "." else f"{relative}/{name}"
            archive.add(path, arcname=arcname, recursive=False)


def _remove_volume(name: str) -> list[str]:
    last = "failed"
    for attempt in range(5):
        try:
            completed = subprocess.run(
                ["docker", "volume", "rm", name],
                shell=False,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except Exception as exc:
            return [f"volume rm {name} failed: {exc.__class__.__name__}"]
        if completed.returncode == 0:
            return []
        detail = (completed.stderr or completed.stdout or "failed").strip().splitlines()
        last = detail[0][:200] if detail else "failed"
        lowered = last.lower()
        if "no such volume" in lowered:
            return []
        if "in use" not in lowered or attempt == 4:
            return [f"docker volume rm {name} failed: {last}"]
        time.sleep(0.2)
    return [f"docker volume rm {name} failed: {last}"]


def _teardown_one(argv: list[str]) -> list[str]:
    try:
        completed = subprocess.run(
            argv,
            shell=False,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except Exception as exc:
        return [f"{argv[1]} {argv[-1]} failed: {exc.__class__.__name__}"]
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "failed").strip().splitlines()
        line = detail[0][:200] if detail else "failed"
        return [f"{' '.join(argv)} failed: {line}"]
    return []
