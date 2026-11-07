"""Trusted fetcher. Local bare repos only — no network."""

from __future__ import annotations

import base64
import gzip
import hashlib
import io
import re
import subprocess
import tarfile
from dataclasses import dataclass
from pathlib import Path

import pytest

import challenge_runner.fetcher as fetcher
from challenge_runner.fetcher import FetchError, fetch_source

# Kept before any test patches subprocess.run on the shared subprocess module.
_RUN = subprocess.run
TOKEN = "sb-s3-sentinel-7f3c9a1e-TOKEN"
_ENCODED = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode("ascii")
_HEADER = f"AUTHORIZATION: basic {_ENCODED}"


_REQUIRE_HOST = fetcher._require_host


@pytest.fixture(autouse=True)
def _local_origins(monkeypatch):
    """Tests clone local bare repos over file://; production accepts only https."""

    def require(git_host: str) -> str:
        if isinstance(git_host, str) and git_host.startswith("file://"):
            return git_host.rstrip("/")
        return _REQUIRE_HOST(git_host)

    monkeypatch.setattr(fetcher, "_require_host", require)


@pytest.mark.parametrize(
    "host", ["http://github.com", "file:///tmp/x", "ssh://git@github.com", "github.com", ""]
)
def test_only_https_hosts_receive_the_credential(host):
    with pytest.raises(FetchError):
        _REQUIRE_HOST(host)
    assert _REQUIRE_HOST("https://github.com/") == "https://github.com"


@pytest.mark.parametrize("repo", ["../widgets", "acme/..", "./widgets", "acme/."])
def test_dot_path_segments_rejected(repo):
    with pytest.raises(FetchError):
        fetcher._require_repo(repo)
    assert fetcher._require_repo(".github/.dotfiles") == ".github/.dotfiles"


def test_inherited_git_settings_are_not_passed_to_git(monkeypatch):
    monkeypatch.setenv("GIT_TRACE_CURL", "1")
    monkeypatch.setenv("GIT_SSL_NO_VERIFY", "1")
    monkeypatch.setenv("GIT_DIR", "/elsewhere")
    env = fetcher._credential_env("encoded")
    assert "GIT_TRACE_CURL" not in env
    assert "GIT_SSL_NO_VERIFY" not in env
    assert "GIT_DIR" not in env
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["GIT_CONFIG_GLOBAL"] == fetcher.os.devnull


@dataclass(frozen=True)
class _Origin:
    git_host: str
    sha_parent: str
    sha_tip: str


def _git(*args: str, cwd: Path | None = None) -> str:
    result = _RUN(
        ["git", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result.stdout.strip()


def _assert_token_absent(root: Path, token: str = TOKEN) -> None:
    if not root.exists():
        return
    needle = token.encode("utf-8")
    for path in [root, *root.rglob("*")]:
        assert token not in path.name
        if path.is_symlink() or not path.is_file():
            continue
        assert needle not in path.read_bytes()


def _tar_files(blob: bytes) -> dict[str, bytes]:
    files: dict[str, bytes] = {}
    with gzip.GzipFile(fileobj=io.BytesIO(blob)) as gz:
        payload = gz.read()
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as archive:
        for member in archive.getmembers():
            name = member.name[2:] if member.name.startswith("./") else member.name
            assert name != ".git"
            assert not name.startswith(".git/")
            if not member.isfile():
                continue
            extracted = archive.extractfile(member)
            assert extracted is not None
            files[name] = extracted.read()
    return files


@pytest.fixture
def origin(tmp_path: Path) -> _Origin:
    host = tmp_path / "host"
    bare = host / "acme" / "widgets.git"
    work = tmp_path / "seed"
    bare.parent.mkdir(parents=True)
    _git("init", "--bare", "-q", str(bare))
    _git("init", "-q", "-b", "main", str(work))
    _git("-C", str(work), "config", "user.email", "fetcher@example.com")
    _git("-C", str(work), "config", "user.name", "Fetcher Test")
    (work / "README").write_text("one\n", encoding="utf-8")
    (work / "src").mkdir()
    (work / "src" / "a.py").write_text("print(1)\n", encoding="utf-8")
    _git("-C", str(work), "add", "README", "src/a.py")
    _git("-C", str(work), "commit", "-q", "-m", "first")
    parent = _git("-C", str(work), "rev-parse", "HEAD")
    (work / "README").write_text("two\n", encoding="utf-8")
    (work / "ONLY2").write_text("second\n", encoding="utf-8")
    _git("-C", str(work), "add", "README", "ONLY2")
    _git("-C", str(work), "commit", "-q", "-m", "second")
    tip = _git("-C", str(work), "rev-parse", "HEAD")
    _git("-C", str(work), "remote", "add", "origin", str(bare))
    _git("-C", str(work), "push", "-q", "origin", "HEAD:refs/heads/main")
    assert len(parent) == 40 and len(tip) == 40 and parent != tip
    return _Origin(git_host="file://" + str(host), sha_parent=parent, sha_tip=tip)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict]]:
    recorded: list[tuple[list[str], dict]] = []
    real = fetcher.subprocess.run

    def spy(argv, **kwargs):
        argv_list = list(argv)
        recorded.append((argv_list, kwargs))
        assert all(TOKEN not in str(part) for part in argv_list)
        assert kwargs.get("shell") is not True
        result = real(argv_list, **kwargs)
        cwd = kwargs.get("cwd")
        if cwd is not None:
            root = Path(cwd)
            config = root / ".git" / "config"
            if config.is_file():
                text = config.read_text(encoding="utf-8", errors="replace").lower()
                assert TOKEN.lower() not in text
                assert "extraheader" not in text
            _assert_token_absent(root)
        return result

    monkeypatch.setattr(fetcher.subprocess, "run", spy)
    return recorded


@pytest.fixture
def clones(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    created: list[str] = []
    real = fetcher.tempfile.mkdtemp

    def spy(*args, **kwargs):
        path = real(*args, **kwargs)
        created.append(path)
        return path

    monkeypatch.setattr(fetcher.tempfile, "mkdtemp", spy)
    yield created
    for path in created:
        assert not Path(path).exists()


def _fetch(origin: _Origin, workdir: Path, sha: str, *, include_history: bool = False):
    return fetch_source(
        repo_full_name="acme/widgets",
        sha=sha,
        token=TOKEN,
        workdir=workdir,
        include_history=include_history,
        git_host=origin.git_host,
    )


def _fetches(recorded: list[tuple[list[str], dict]]) -> list[list[str]]:
    return [
        argv for argv, _ in recorded if len(argv) >= 2 and argv[0] == "git" and argv[1] == "fetch"
    ]


def _assert_command_discipline(recorded: list[tuple[list[str], dict]], *, depth: bool) -> None:
    fetches = _fetches(recorded)
    assert fetches
    for argv, kwargs in recorded:
        joined = "\0".join(str(part) for part in argv)
        assert TOKEN not in joined
        assert _ENCODED not in joined
        assert kwargs.get("shell") is not True
        env = kwargs["env"]
        if argv[:2] == ["git", "fetch"]:
            assert kwargs["timeout"] == 300
            assert env["GIT_CONFIG_COUNT"] == "2"
            assert env["GIT_CONFIG_KEY_0"] == "http.extraHeader"
            assert env["GIT_CONFIG_VALUE_0"] == _HEADER
            assert env["GIT_CONFIG_KEY_1"] == "credential.helper"
            assert env["GIT_CONFIG_VALUE_1"] == ""
            assert env["GIT_TERMINAL_PROMPT"] == "0"
            assert env["GIT_ASKPASS"] == "/bin/false"
            assert ("--depth" in argv) is depth
        else:
            assert kwargs["timeout"] == 60
            blob = "\n".join(env.values())
            assert TOKEN not in blob
            assert _ENCODED not in blob


def test_tarball_matches_tree_and_is_reproducible(origin: _Origin, tmp_path: Path, calls, clones):
    workdir = tmp_path / "out"
    parent = _fetch(origin, workdir, origin.sha_parent)
    parent_bytes = parent.source_tarball.read_bytes()
    assert parent.history_bundle is None
    assert parent.history_digest is None
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", parent.source_digest)
    assert parent.source_digest == "sha256:" + hashlib.sha256(parent_bytes).hexdigest()
    files = _tar_files(parent_bytes)
    assert files["README"] == b"one\n"
    assert files["src/a.py"] == b"print(1)\n"
    assert "ONLY2" not in files

    tip = _fetch(origin, workdir, origin.sha_tip)
    tip_bytes = tip.source_tarball.read_bytes()
    assert _tar_files(tip_bytes)["README"] == b"two\n"
    assert _tar_files(tip_bytes)["ONLY2"] == b"second\n"
    assert tip_bytes != parent_bytes

    again = _fetch(origin, workdir, origin.sha_parent.upper())
    assert again.source_tarball.read_bytes() == parent_bytes
    assert again.source_digest == parent.source_digest
    _assert_token_absent(workdir)
    assert [path.name for path in workdir.iterdir()] == ["source.tar.gz"]
    assert clones
    assert all(Path(path).parent == workdir for path in clones)
    url = f"{origin.git_host}/acme/widgets.git"
    for argv in _fetches(calls):
        assert "--depth" in argv and "1" in argv
        assert url in argv
        assert origin.sha_parent in argv or origin.sha_tip in argv
    assert any(origin.sha_parent + "^{commit}" in argv for argv, _ in calls)
    assert any("--format=tar" in argv and origin.sha_parent in argv for argv, _ in calls)
    _assert_command_discipline(calls, depth=True)


def test_history_bundle_verifies_and_contains_sha(origin: _Origin, tmp_path: Path, calls, clones):
    workdir = tmp_path / "out"
    out = _fetch(origin, workdir, origin.sha_tip, include_history=True)
    assert out.history_bundle is not None and out.history_digest is not None
    bundle = out.history_bundle.read_bytes()
    assert out.history_digest == "sha256:" + hashlib.sha256(bundle).hexdigest()
    assert TOKEN.encode() not in bundle
    assert b"extraheader" not in bundle.lower()
    assert b"[remote " not in bundle
    verify = _RUN(
        ["git", "bundle", "verify", str(out.history_bundle)],
        check=False,
        capture_output=True,
        text=True,
    )
    assert verify.returncode == 0, verify.stderr
    combined = verify.stdout + verify.stderr
    assert origin.sha_tip in combined
    assert "complete history" in combined
    heads = _RUN(
        ["git", "bundle", "list-heads", str(out.history_bundle)],
        check=True,
        capture_output=True,
        text=True,
    )
    lines = [line.split() for line in heads.stdout.splitlines() if line.strip()]
    assert any(tip == origin.sha_tip for tip, _ref in lines)
    ref = next(ref for tip, ref in lines if tip == origin.sha_tip)
    dest = tmp_path / "unbundled"
    _git("init", "-q", "-b", "main", str(dest))
    _git("-C", str(dest), "fetch", "-q", str(out.history_bundle), ref)
    count = _git("-C", str(dest), "rev-list", "--count", "FETCH_HEAD")
    assert count == "2"
    assert _tar_files(out.source_tarball.read_bytes())["ONLY2"] == b"second\n"
    _assert_token_absent(workdir)
    assert not any(path.is_dir() for path in workdir.iterdir())
    fetches = _fetches(calls)
    assert fetches and all("--depth" not in argv for argv in fetches)
    assert any(
        argv[:3] == ["git", "bundle", "create"] and argv[3].endswith("history.bundle")
        for argv, _ in calls
    )
    _assert_command_discipline(calls, depth=False)
    assert clones


def test_wrong_sha_raises_and_removes_clone(origin: _Origin, tmp_path: Path, calls, clones):
    workdir = tmp_path / "out"
    wrong = "deadbeef" * 5
    with pytest.raises(FetchError) as excinfo:
        _fetch(origin, workdir, wrong)
    assert TOKEN not in str(excinfo.value)
    assert _ENCODED not in str(excinfo.value)
    assert list(workdir.iterdir()) == []
    _assert_token_absent(workdir)
    assert clones
    _assert_command_discipline(calls, depth=True)


@pytest.mark.parametrize(
    ("repo", "sha"),
    [
        ("acme", "a" * 40),
        ("acme/widgets/extra", "a" * 40),
        ("acme/wid gets", "a" * 40),
        ("/acme/widgets", "a" * 40),
        ("", "a" * 40),
        ("..", "a" * 40),
        ("acme/widgets", ""),
        ("acme/widgets", "abc"),
        ("acme/widgets", "g" * 40),
        ("acme/widgets", "a" * 39),
        ("acme/widgets", "a" * 41),
        ("acme/widgets", "a" * 39 + "g"),
        ("acme/widgets", "a" * 40 + "\n"),
    ],
)
def test_invalid_repo_or_sha_rejected_before_subprocess(tmp_path: Path, monkeypatch, repo, sha):
    def boom(*_args, **_kwargs):
        raise AssertionError("subprocess was called")

    monkeypatch.setattr(fetcher.subprocess, "run", boom)
    workdir = tmp_path / "work"
    with pytest.raises(FetchError) as excinfo:
        fetch_source(
            repo_full_name=repo,
            sha=sha,
            token=TOKEN,
            workdir=workdir,
            git_host="file:///unused",
        )
    assert TOKEN not in str(excinfo.value)
    assert not workdir.exists()


def test_newline_token_rejected_before_subprocess(tmp_path: Path, monkeypatch):
    def boom(*_args, **_kwargs):
        raise AssertionError("subprocess was called")

    monkeypatch.setattr(fetcher.subprocess, "run", boom)
    with pytest.raises(FetchError) as excinfo:
        fetch_source(
            repo_full_name="acme/widgets",
            sha="a" * 40,
            token="abc\ndef",
            workdir=tmp_path / "work",
        )
    assert "abc\ndef" not in str(excinfo.value)


def test_error_scrubs_token_and_drops_clone(tmp_path: Path, monkeypatch, clones):
    encoded = _ENCODED

    def fake_run(argv, **_kwargs):
        assert TOKEN not in "\0".join(str(part) for part in argv)
        stderr = f"fatal: {TOKEN} {encoded}".encode()
        return subprocess.CompletedProcess(list(argv), 1, b"", stderr)

    monkeypatch.setattr(fetcher.subprocess, "run", fake_run)
    workdir = tmp_path / "out"
    with pytest.raises(FetchError) as excinfo:
        fetch_source(
            repo_full_name="acme/widgets",
            sha="a" * 40,
            token=TOKEN,
            workdir=workdir,
        )
    message = str(excinfo.value)
    assert TOKEN not in message
    assert encoded not in message
    assert "fatal:" in message
    assert list(workdir.iterdir()) == []
    assert clones
