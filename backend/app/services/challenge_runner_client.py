"""Invoke ``CHALLENGE_RUNNER_PYTHON -m challenge_runner`` and parse its JSON.

The sandbox job is a file. A fetch token is one stdin line for ``fetch`` and is
never placed in argv, the environment, the job file, or an error message.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from app.config import settings
from app.schemas.challenge import RunnerJob, RunnerResult

_MAX_RESULT_BYTES = 1_000_000
# Installation tokens are minted for the trusted fetcher only. A sandbox job that
# carries one is refused before the process starts.
_FORBIDDEN_KEYS = frozenset(
    {"token", "secret", "password", "authorization", "access_token", "private_key"}
)
_TOKEN_MARKERS = ("ghs_", "ghp_", "gho_", "github_pat_")
_REPO_OK = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_SHA_OK = re.compile(r"^[0-9a-fA-F]{40}$")


class RunnerInvocationError(Exception):
    """The runner process failed. The message is safe to log."""


@dataclass(frozen=True)
class FetchResult:
    source_tarball: str
    source_digest: str
    history_bundle: str | None = None
    history_digest: str | None = None


def _runner_root() -> Path:
    return Path(__file__).resolve().parents[3] / "challenge-runner"


def _subprocess_env() -> dict[str, str]:
    """Environment for the host runner. Token variables are not copied."""
    env: dict[str, str] = {}
    for key in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "TMPDIR", "TMP", "TEMP"):
        value = os.environ.get(key)
        if value:
            env[key] = value
    env["PYTHONPATH"] = str(_runner_root())
    env["PYTHONNOUSERSITE"] = "1"
    if settings.CHALLENGE_SANDBOX_IMAGE:
        env["CHALLENGE_SANDBOX_IMAGE"] = settings.CHALLENGE_SANDBOX_IMAGE
    if settings.CHALLENGE_SANDBOX_RUNTIME:
        env["CHALLENGE_SANDBOX_RUNTIME"] = settings.CHALLENGE_SANDBOX_RUNTIME
    return env


def _reject_secret_material(value: object, *, where: str) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if str(key).casefold() in _FORBIDDEN_KEYS:
                raise RunnerInvocationError(f"runner job rejected: {where}")
            _reject_secret_material(item, where=where)
        return
    if isinstance(value, list):
        for item in value:
            _reject_secret_material(item, where=where)
        return
    if isinstance(value, str) and any(marker in value for marker in _TOKEN_MARKERS):
        raise RunnerInvocationError(f"runner job rejected: {where}")


def _python() -> str:
    return settings.CHALLENGE_RUNNER_PYTHON or "python3"


def _run(cmd: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=3600,
            env=_subprocess_env(),
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RunnerInvocationError("challenge runner timed out") from None
    except OSError:
        raise RunnerInvocationError("challenge runner failed") from None


def _parse_result(path: Path) -> RunnerResult:
    try:
        size = path.stat().st_size
    except OSError:
        raise RunnerInvocationError("challenge runner failed") from None
    if size <= 0 or size > _MAX_RESULT_BYTES:
        raise RunnerInvocationError("challenge runner failed")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunnerResult.model_validate(data)
    except (OSError, json.JSONDecodeError, ValidationError):
        raise RunnerInvocationError("challenge runner failed") from None


def run_runner(job: RunnerJob, *, artifact_root: str | None = None) -> RunnerResult:
    """Run one sandbox stage. ``job`` is written to a file; it cannot carry a token."""
    payload = job.model_dump(mode="json")
    _reject_secret_material(payload, where="sandbox job")
    root = artifact_root or settings.CHALLENGE_ARTIFACT_ROOT
    with tempfile.TemporaryDirectory(prefix="chal-job-") as tmp:
        job_path = Path(tmp) / "job.json"
        out_path = Path(tmp) / "result.json"
        job_path.write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
        cmd = [
            _python(),
            "-m",
            "challenge_runner",
            "run",
            "--job",
            str(job_path),
            "--out",
            str(out_path),
            "--artifact-root",
            root,
        ]
        _run(cmd)
        if not out_path.is_file():
            raise RunnerInvocationError("challenge runner failed")
        return _parse_result(out_path)


def fetch_source(
    *,
    repo_full_name: str,
    sha: str,
    token: str,
    workdir: str,
    include_history: bool = False,
) -> FetchResult:
    """Fetch one revision. ``token`` is the stdin line and does not appear in argv."""
    if not isinstance(token, str) or token == "":
        raise RunnerInvocationError("source fetch failed")
    if not _REPO_OK.fullmatch(repo_full_name) or not _SHA_OK.fullmatch(sha):
        raise RunnerInvocationError("source fetch rejected")
    _reject_secret_material({"repo": repo_full_name, "sha": sha}, where="fetch")
    cmd = [
        _python(),
        "-m",
        "challenge_runner",
        "fetch",
        "--repo",
        repo_full_name,
        "--sha",
        sha,
        "--workdir",
        workdir,
        "--artifact-root",
        settings.CHALLENGE_ARTIFACT_ROOT,
    ]
    if include_history:
        cmd.append("--history")
    # The token is the first stdin line, matching challenge_runner fetch.
    completed = _run(cmd, input_text=token + "\n")
    if completed.returncode != 0:
        raise RunnerInvocationError("source fetch failed")
    return _parse_fetch(completed.stdout)


def _parse_fetch(stdout: str) -> FetchResult:
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError:
        raise RunnerInvocationError("source fetch failed") from None
    if not isinstance(data, dict):
        raise RunnerInvocationError("source fetch failed")
    source = data.get("source_digest")
    path = data.get("source_tarball")
    if not isinstance(source, str) or not isinstance(path, str) or path == "":
        raise RunnerInvocationError("source fetch failed")
    history = data.get("history_bundle")
    history_digest = data.get("history_digest")
    if history is not None and not isinstance(history, str):
        raise RunnerInvocationError("source fetch failed")
    if history_digest is not None and not isinstance(history_digest, str):
        raise RunnerInvocationError("source fetch failed")
    return FetchResult(
        source_tarball=path,
        source_digest=source,
        history_bundle=history,
        history_digest=history_digest,
    )
