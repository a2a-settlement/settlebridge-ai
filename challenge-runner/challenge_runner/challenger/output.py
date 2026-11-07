"""Write claim artifacts and a deterministic challenger bundle archive."""

from __future__ import annotations

import gzip
import io
import json
import os
import stat
import tarfile
from pathlib import Path

from challenge_runner.challenger.errors import ChallengerError
from challenge_runner.challenger.limits import (
    ARCHIVE_NAME,
    FIXED_TAR_GID,
    FIXED_TAR_MODE,
    FIXED_TAR_MTIME,
    FIXED_TAR_UID,
    REASON_CODES,
)
from challenge_runner.challenger.paths import normalize_test_path


class _StoreReject(Exception):
    def __init__(self, code: str):
        self.code = code


def prepare_output(out_dir: Path) -> Path:
    if not isinstance(out_dir, Path):
        raise ChallengerError("out_dir must be a path")
    if out_dir.is_symlink():
        raise ChallengerError("refusing to follow a symlinked output directory")
    out_dir.mkdir(parents=True, exist_ok=True)
    root = out_dir.resolve()
    if out_dir.is_symlink() or not root.is_dir():
        raise ChallengerError("refusing to follow a symlinked output directory")
    tests = root / "challenge_tests"
    if tests.is_symlink():
        raise ChallengerError("refusing to follow a symlinked challenge_tests directory")
    tests.mkdir(exist_ok=True)
    if tests.is_symlink() or not tests.is_dir() or not tests.resolve().is_relative_to(root):
        raise ChallengerError("refusing to follow a symlinked challenge_tests directory")
    return root


def model_tokens_from_client(client: object) -> int:
    """Sum input and output tokens. Spend stores ``model_tokens`` as one integer."""
    usage = getattr(client, "last_usage", None)
    if not isinstance(usage, dict):
        return 0
    return _token_count(usage.get("input_tokens")) + _token_count(usage.get("output_tokens"))


def persist_outputs(
    out_dir: Path,
    *,
    claims: list[dict],
    rejected: dict,
    calls: int,
    model_tokens: int,
) -> None:
    write_json(out_dir / "claims.json", claims)
    write_json(out_dir / "rejected.json", public_rejection(rejected))
    write_json(
        out_dir / "usage.json",
        {"calls": calls, "model_tokens": model_tokens},
    )


def public_rejection(rejected: dict) -> dict:
    reasons_in = rejected.get("reasons") if isinstance(rejected, dict) else None
    clean: dict[str, int] = {}
    if isinstance(reasons_in, dict):
        for key, value in reasons_in.items():
            if key not in REASON_CODES or isinstance(value, bool) or not isinstance(value, int):
                continue
            if value > 0:
                clean[key] = value
    return {"count": sum(clean.values()), "reasons": clean}


def write_json(path: Path, payload: object) -> None:
    if path.is_symlink():
        raise ChallengerError("refusing to follow a symlinked output file")
    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    path.write_text(text, encoding="utf-8")


def store_test_files(out_dir: Path, claims: list[dict]) -> tuple[list[dict], dict]:
    reasons: dict[str, int] = {}
    kept: list[dict] = []
    stored: dict[str, bytes] = {}
    for claim in claims:
        created: list[str] = []
        try:
            payloads = _payloads(claim)
            for rel, blob in payloads:
                previous = stored.get(rel)
                if previous is not None and previous != blob:
                    raise _StoreReject("duplicate_test_path")
            for rel, blob in payloads:
                if rel in stored:
                    continue
                _write_file(out_dir, rel, blob)
                stored[rel] = blob
                created.append(rel)
        except _StoreReject as exc:
            for rel in reversed(created):
                _unlink_file(out_dir, rel)
                stored.pop(rel, None)
            reasons[exc.code] = reasons.get(exc.code, 0) + 1
            continue
        kept.append(claim)
    return kept, {"count": sum(reasons.values()), "reasons": reasons}


def package_bundle(out_dir: Path) -> Path:
    """Write a deterministic gzip of the regular files under ``challenge_tests/``."""
    root = prepare_output(out_dir)
    destination = root / ARCHIVE_NAME
    if destination.is_symlink():
        raise ChallengerError("refusing to follow a symlinked bundle archive")
    payload = _archive_bytes(_regular_files(root))
    destination.write_bytes(payload)
    return destination


def _payloads(claim: dict) -> list[tuple[str, bytes]]:
    files = claim.get("test_files")
    if not isinstance(files, list) or not files:
        raise _StoreReject("missing_test_file")
    payloads: list[tuple[str, bytes]] = []
    for item in files:
        if not isinstance(item, dict):
            raise _StoreReject("invalid_type")
        rel = normalize_test_path(item.get("path"))
        content = item.get("content")
        if rel is None:
            raise _StoreReject("unsafe_test_path")
        if not isinstance(content, str):
            raise _StoreReject("invalid_type")
        try:
            blob = content.encode("utf-8")
        except UnicodeError as exc:
            raise _StoreReject("invalid_test_content") from exc
        payloads.append((rel, blob))
    return payloads


def _write_file(out_dir: Path, rel: str, data: bytes) -> None:
    target = _ensure_parents(out_dir, rel)
    if target.exists() or target.is_symlink():
        if target.is_symlink() or not _same_bytes(target, data):
            raise _StoreReject("duplicate_test_path")
        return
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    try:
        fd = os.open(target, flags, 0o644)
    except FileExistsError as exc:
        if _same_bytes(target, data):
            return
        raise _StoreReject("duplicate_test_path") from exc
    except OSError:
        raise _StoreReject("write_failed") from None
    try:
        view = memoryview(data)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise _StoreReject("write_failed")
            view = view[written:]
        os.fsync(fd)
    except _StoreReject:
        os.close(fd)
        target.unlink(missing_ok=True)
        raise
    except OSError:
        os.close(fd)
        target.unlink(missing_ok=True)
        raise _StoreReject("write_failed") from None
    os.close(fd)
    os.chmod(target, 0o644)


def _ensure_parents(out_dir: Path, rel: str) -> Path:
    root = out_dir.resolve()
    current = root
    parts = rel.split("/")
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise _StoreReject("unsafe_test_path")
        if current.exists():
            if not current.is_dir() or not current.resolve().is_relative_to(root):
                raise _StoreReject("unsafe_test_path")
            continue
        current.mkdir(mode=0o755)
        if current.is_symlink() or not current.is_dir():
            raise _StoreReject("unsafe_test_path")
        if not current.resolve().is_relative_to(root):
            raise _StoreReject("unsafe_test_path")
    target = current / parts[-1]
    if target.is_symlink() or not current.resolve().is_relative_to(root):
        raise _StoreReject("unsafe_test_path")
    if target.exists() and not target.resolve().is_relative_to(root):
        raise _StoreReject("unsafe_test_path")
    return target


def _same_bytes(path: Path, expected: bytes) -> bool:
    if path.is_symlink():
        return False
    try:
        info = path.lstat()
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return False
    try:
        chunks: list[bytes] = []
        remaining = len(expected) + 1
        while remaining > 0:
            block = os.read(fd, min(8192, remaining))
            if not block:
                break
            chunks.append(block)
            remaining -= len(block)
    finally:
        os.close(fd)
    return b"".join(chunks) == expected


def _unlink_file(out_dir: Path, rel: str) -> None:
    target = out_dir.joinpath(*rel.split("/"))
    if target.is_symlink():
        return
    try:
        if target.is_file() and target.resolve().is_relative_to(out_dir.resolve()):
            target.unlink()
    except OSError:
        return


def _regular_files(root: Path) -> list[tuple[str, bytes]]:
    tests = root / "challenge_tests"
    if tests.is_symlink() or not tests.is_dir():
        raise ChallengerError("refusing to follow a symlinked challenge_tests directory")
    found: list[tuple[str, bytes]] = []
    for dirpath, dirnames, filenames in os.walk(tests, followlinks=False):
        current = Path(dirpath)
        dirnames[:] = sorted(name for name in dirnames if not (current / name).is_symlink())
        for name in sorted(filenames):
            path = current / name
            blob = _read_regular(tests, path)
            if blob is None:
                continue
            rel = "challenge_tests/" + path.relative_to(tests).as_posix()
            if normalize_member(rel) is None:
                continue
            found.append((rel, blob))
    found.sort(key=lambda item: item[0])
    return found


def normalize_member(rel: str) -> str | None:
    if not rel.startswith("challenge_tests/") or ".." in rel.split("/"):
        return None
    if "\\" in rel or "\x00" in rel or rel.startswith("/"):
        return None
    return rel


def _read_regular(tests_root: Path, path: Path) -> bytes | None:
    if path.is_symlink():
        return None
    try:
        info = path.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(info.st_mode):
        return None
    try:
        resolved = path.resolve()
    except OSError:
        return None
    if not resolved.is_file() or not resolved.is_relative_to(tests_root.resolve()):
        return None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        return None
    try:
        chunks: list[bytes] = []
        while True:
            block = os.read(fd, 1024 * 1024)
            if not block:
                break
            chunks.append(block)
    finally:
        os.close(fd)
    return b"".join(chunks)


def _archive_bytes(members: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    with gzip.GzipFile(
        fileobj=buffer, mode="wb", filename="", mtime=FIXED_TAR_MTIME, compresslevel=9
    ) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.USTAR_FORMAT) as archive:
            for name, data in members:
                info = tarfile.TarInfo(name=name)
                info.size = len(data)
                info.mtime = FIXED_TAR_MTIME
                info.mode = FIXED_TAR_MODE
                info.uid = FIXED_TAR_UID
                info.gid = FIXED_TAR_GID
                info.uname = ""
                info.gname = ""
                info.type = tarfile.REGTYPE
                info.pax_headers = {}
                archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def _token_count(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    if value < 0:
        return 0
    return value
