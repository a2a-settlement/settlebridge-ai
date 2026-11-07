"""Docker argv construction and process control for the gVisor sandbox.

The runner never invokes a shell. Image and runtime come from the environment.
``--rm`` is intentionally absent; teardown removes containers explicitly.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import subprocess
from collections.abc import Sequence
from pathlib import Path

from challenge_runner.protocol import DIGEST_RE, RunnerJob

PINNED_IMAGE_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,200}"
    r"(?::[A-Za-z0-9._-]{1,128})?"
    r"@sha256:[0-9a-f]{64}$"
)
RUNTIME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,32}$")
TMPFS_SPEC = "/tmp:rw,noexec,nosuid,size=256m"
INFO_FORMAT = "{{json .Runtimes}}"
IMAGE_DIGEST_FORMAT = "{{index .RepoDigests 0}}"
NETWORK_FORMAT = "{{json .NetworkSettings.Networks}}"
STATE_FORMAT = "{{.State.OOMKilled}}|{{.State.ExitCode}}"
PROXY_PORT = 8080
PIDS_OVERHEAD = 256
_FALLBACK_IMAGE = "sha256:" + hashlib.sha256(b"sandbox-unavailable").hexdigest()


class SandboxUnavailable(Exception):
    """Docker or the runsc runtime is missing; the caller reports sandbox_error."""


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


def package_mounts(work_dir: str) -> list[str]:
    return [
        "-v",
        f"{package_dir()}:/opt/sbxpkg/challenge_runner:ro",
        "-v",
        f"{work_dir}:/work:rw",
    ]


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
    argv: Sequence[str], *, timeout: float, kill_name: str
) -> tuple[int | None, str, str, bool]:
    """Run a container. Wall-clock expiry kills it and reports timed_out."""
    assert_argv_safe(argv)
    try:
        proc = subprocess.Popen(
            list(argv),
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except FileNotFoundError as exc:
        raise SandboxUnavailable("sandbox runtime unavailable") from exc
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        return proc.returncode, stdout or "", stderr or "", False
    except subprocess.TimeoutExpired:
        _kill(kill_name)
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            stdout, stderr = proc.communicate()
        return proc.returncode, stdout or "", stderr or "", True


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
) -> list[str]:
    """Remove containers, then any per-run networks. Failures are recorded."""
    notes: list[str] = []
    for name in (container, proxy):
        if not name:
            continue
        notes.extend(_teardown_one(["docker", "rm", "-f", name]))
    for net in (network, egress_network):
        if net:
            notes.extend(_teardown_one(["docker", "network", "rm", net]))
    return notes


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
