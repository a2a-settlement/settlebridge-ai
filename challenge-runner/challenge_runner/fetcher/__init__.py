"""Trusted fetcher (owner: S3). Runs outside every sandbox.

Clones with a narrowed token supplied by the caller, exports a source tarball with
``git archive`` at the pinned SHA (plus, when asked, a history bundle with no remote
configuration), scrubs ``.git/config`` and any extraheader, and discards the token.
The token never reaches a sandbox, an environment variable of a child sandbox, a
mount, a log, or the returned values.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

_REPO_RE = re.compile(r"^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_FETCH_TIMEOUT_S = 300
_COMMAND_TIMEOUT_S = 60
# git bundle create refuses a raw object id; it only packages a named ref.
_FETCHED_REF = "refs/heads/fetched"
_REDACTED = "REDACTED"


@dataclass(frozen=True)
class FetchOutput:
    source_tarball: Path
    source_digest: str  # sha256:<hex> of the tarball bytes
    history_bundle: Path | None
    history_digest: str | None


class FetchError(Exception):
    pass


def fetch_source(
    *,
    repo_full_name: str,
    sha: str,
    token: str,
    workdir: Path,
    include_history: bool = False,
    git_host: str = "https://github.com",
) -> FetchOutput:
    repo = _require_repo(repo_full_name)
    host = _require_host(git_host)
    commit = _require_sha(sha)
    secret = _require_token(token)
    encoded = _basic_credential(secret)
    try:
        workdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise FetchError(_scrub(f"workdir is not usable: {exc}", secret, encoded)) from None
    clone_dir = Path(tempfile.mkdtemp(prefix="clone-", dir=workdir))
    try:
        return _export(
            clone_dir=clone_dir,
            workdir=workdir,
            repo=repo,
            commit=commit,
            secret=secret,
            encoded=encoded,
            include_history=include_history,
            git_host=host,
        )
    finally:
        shutil.rmtree(clone_dir)


def _require_repo(repo_full_name: str) -> str:
    if not isinstance(repo_full_name, str) or _REPO_RE.fullmatch(repo_full_name) is None:
        raise FetchError("invalid repository")
    if any(part in (".", "..") for part in repo_full_name.split("/")):
        raise FetchError("invalid repository")
    return repo_full_name


def _require_host(git_host: str) -> str:
    # The credential header goes to this host, so it must be TLS.
    if not isinstance(git_host, str) or not git_host.startswith("https://"):
        raise FetchError("git host must use https")
    return git_host.rstrip("/")


def _require_sha(sha: str) -> str:
    if not isinstance(sha, str) or _SHA_RE.fullmatch(sha) is None:
        raise FetchError("invalid commit sha")
    return sha.lower()


def _require_token(token: str) -> str:
    if not isinstance(token, str) or any(char in token for char in "\x00\r\n"):
        raise FetchError("invalid token")
    return token


def _basic_credential(token: str) -> str:
    return base64.b64encode(f"x-access-token:{token}".encode("utf-8")).decode("ascii")


def _scrub(text: str, *secrets: str) -> str:
    for secret in sorted({item for item in secrets if item}, key=len, reverse=True):
        text = text.replace(secret, _REDACTED)
    return text


def _as_text(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def _base_env() -> dict[str, str]:
    # Inherited GIT_* (GIT_TRACE_CURL, GIT_SSL_NO_VERIFY, GIT_DIR, ...) could log the
    # credential, weaken TLS, or redirect the repository.
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    # A global or system url.insteadOf could send the credential header elsewhere.
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_ASKPASS"] = "/bin/false"
    return env


def _plain_env() -> dict[str, str]:
    env = _base_env()
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "credential.helper"
    env["GIT_CONFIG_VALUE_0"] = ""
    return env


def _credential_env(encoded: str) -> dict[str, str]:
    env = _base_env()
    env["GIT_CONFIG_COUNT"] = "2"
    env["GIT_CONFIG_KEY_0"] = "http.extraHeader"
    env["GIT_CONFIG_VALUE_0"] = f"AUTHORIZATION: basic {encoded}"
    env["GIT_CONFIG_KEY_1"] = "credential.helper"
    env["GIT_CONFIG_VALUE_1"] = ""
    return env


def _git(
    argv: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str],
    secret: str,
    encoded: str,
) -> subprocess.CompletedProcess[bytes]:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        detail = _as_text(exc.stderr if isinstance(exc.stderr, (bytes, str)) else None)
        raise FetchError(_scrub(f"git command timed out: {detail}", secret, encoded)) from None
    except OSError as exc:
        raise FetchError(_scrub(f"git command failed to start: {exc}", secret, encoded)) from None
    if result.returncode != 0:
        detail = _as_text(result.stderr)
        raise FetchError(_scrub(f"git command failed: {detail}", secret, encoded)) from None
    return result


def _digest(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _export(
    *,
    clone_dir: Path,
    workdir: Path,
    repo: str,
    commit: str,
    secret: str,
    encoded: str,
    include_history: bool,
    git_host: str,
) -> FetchOutput:
    plain = _plain_env()
    _git(
        ["git", "init", "--quiet"],
        cwd=clone_dir,
        timeout=_COMMAND_TIMEOUT_S,
        env=plain,
        secret=secret,
        encoded=encoded,
    )
    url = f"{git_host.rstrip('/')}/{repo}.git"
    fetch = ["git", "fetch"]
    if not include_history:
        fetch.extend(["--depth", "1"])
    fetch.extend([url, commit])
    _git(
        fetch,
        cwd=clone_dir,
        timeout=_FETCH_TIMEOUT_S,
        env=_credential_env(encoded),
        secret=secret,
        encoded=encoded,
    )
    parsed = _git(
        ["git", "rev-parse", commit + "^{commit}"],
        cwd=clone_dir,
        timeout=_COMMAND_TIMEOUT_S,
        env=plain,
        secret=secret,
        encoded=encoded,
    )
    if parsed.stdout.decode("ascii", errors="replace").strip() != commit:
        raise FetchError("fetched commit does not match the requested sha")
    archived = _git(
        ["git", "archive", "--format=tar", commit],
        cwd=clone_dir,
        timeout=_COMMAND_TIMEOUT_S,
        env=plain,
        secret=secret,
        encoded=encoded,
    )
    payload = gzip.compress(archived.stdout, mtime=0)
    tarball = workdir / "source.tar.gz"
    tarball.write_bytes(payload)
    history_bundle: Path | None = None
    history_digest: str | None = None
    if include_history:
        _git(
            ["git", "update-ref", _FETCHED_REF, commit],
            cwd=clone_dir,
            timeout=_COMMAND_TIMEOUT_S,
            env=plain,
            secret=secret,
            encoded=encoded,
        )
        history_bundle = workdir / "history.bundle"
        _git(
            ["git", "bundle", "create", str(history_bundle), _FETCHED_REF],
            cwd=clone_dir,
            timeout=_COMMAND_TIMEOUT_S,
            env=plain,
            secret=secret,
            encoded=encoded,
        )
        history_digest = _digest(history_bundle.read_bytes())
    return FetchOutput(
        source_tarball=tarball,
        source_digest=_digest(payload),
        history_bundle=history_bundle,
        history_digest=history_digest,
    )
