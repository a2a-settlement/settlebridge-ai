"""Safe tar extraction for untrusted source and challenger archives."""

from __future__ import annotations

import os
import tarfile
from pathlib import Path, PurePosixPath

_ALLOWED_TYPES = {
    tarfile.REGTYPE,
    tarfile.AREGTYPE,
    tarfile.DIRTYPE,
    tarfile.SYMTYPE,
    tarfile.LNKTYPE,
}
_DEVICE_TYPES = {tarfile.CHRTYPE, tarfile.BLKTYPE}


class UnsafeArchive(ValueError):
    pass


class DiskLimitExceeded(Exception):
    pass


def safe_extract(
    archive: Path,
    dest: Path,
    *,
    required_prefix: str | None = None,
    max_bytes: int | None = None,
) -> None:
    """Extract ``archive`` into ``dest``.

    Rejects absolute paths, ``..``, links that escape ``dest``, and device files.
    When ``required_prefix`` is set, every member must live under that directory.
    """
    dest.mkdir(parents=True, exist_ok=True)
    dest_root = dest.resolve()
    try:
        tar = tarfile.open(archive, "r:*")
    except (tarfile.TarError, OSError) as exc:
        raise UnsafeArchive("refusing unsafe path") from exc
    try:
        _extract_members(tar, dest_root, required_prefix, max_bytes)
    except (DiskLimitExceeded, UnsafeArchive):
        raise
    except (OSError, tarfile.TarError) as exc:
        raise UnsafeArchive("refusing unsafe path") from exc
    finally:
        tar.close()


def _extract_members(tar, dest_root, required_prefix, max_bytes) -> None:
    members = tar.getmembers()
    if len(members) > 10000:
        raise UnsafeArchive("refusing unsafe path")
    planned: list[tuple[tarfile.TarInfo, str]] = []
    for member in members:
        rel = _validate_member(member, required_prefix)
        planned.append((member, rel))
    written = 0
    links: list[tuple[tarfile.TarInfo, str]] = []
    for member, rel in planned:
        if member.isdir():
            _mkdir(dest_root, rel)
            continue
        if member.isreg() or member.type == tarfile.AREGTYPE:
            written = _write_file(tar, member, dest_root, rel, written, max_bytes)
            continue
        if member.issym() or member.islnk():
            links.append((member, rel))
            continue
        raise UnsafeArchive("refusing special file")
    for member, rel in links:
        if member.issym():
            _make_symlink(dest_root, rel, member.linkname)
        else:
            _make_hardlink(dest_root, rel, member.linkname)


def _validate_member(member: tarfile.TarInfo, required_prefix: str | None) -> str:
    if member.ischr() or member.isblk() or member.type in _DEVICE_TYPES:
        raise UnsafeArchive("refusing device file")
    if member.type not in _ALLOWED_TYPES:
        raise UnsafeArchive("refusing special file")
    rel = _normalize_name(member.name)
    if required_prefix is not None and not _under_prefix(rel, member, required_prefix):
        raise UnsafeArchive("challenger bundle path outside challenge_tests")
    if member.issym():
        target = _resolved_link(rel, member.linkname or "")
        if target is None:
            raise UnsafeArchive("refusing symlink escape")
        if required_prefix is not None and not _rel_under_prefix(target, required_prefix):
            raise UnsafeArchive("challenger bundle path outside challenge_tests")
    if member.islnk():
        try:
            target = _normalize_name(member.linkname or "")
        except UnsafeArchive as exc:
            raise UnsafeArchive("refusing hardlink escape") from exc
        if required_prefix is not None and not (
            target == required_prefix or target.startswith(required_prefix + "/")
        ):
            raise UnsafeArchive("refusing hardlink escape")
    return rel


def _normalize_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise UnsafeArchive("refusing unsafe path")
    pure = PurePosixPath(name)
    if pure.is_absolute() or name.startswith("/"):
        raise UnsafeArchive("refusing absolute path")
    parts: list[str] = []
    for part in pure.parts:
        if part in (".",):
            continue
        if part == "..":
            raise UnsafeArchive("refusing parent path")
        if not part or part in ("/", "") or "\x00" in part:
            raise UnsafeArchive("refusing unsafe path")
        parts.append(part)
    if not parts:
        raise UnsafeArchive("refusing unsafe path")
    return "/".join(parts)


def _under_prefix(rel: str, member: tarfile.TarInfo, prefix: str) -> bool:
    if rel == prefix:
        return member.isdir()
    return rel.startswith(prefix + "/")


def _resolved_link(member_rel: str, linkname: str) -> str | None:
    if not linkname or "\x00" in linkname or "\\" in linkname or linkname.startswith("/"):
        return None
    parent = PurePosixPath(member_rel).parent
    parts: list[str] = []
    for part in PurePosixPath(parent, linkname).parts:
        if part in (".",):
            continue
        if part == "..":
            if not parts:
                return None
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _rel_under_prefix(rel: str, prefix: str) -> bool:
    return rel == prefix or rel.startswith(prefix + "/")


def _symlink_stays_inside(member_rel: str, linkname: str) -> bool:
    return _resolved_link(member_rel, linkname) is not None


def _mkdir(dest_root: Path, rel: str) -> None:
    current = dest_root
    for part in rel.split("/"):
        current = current / part
        if current.is_symlink():
            raise UnsafeArchive("refusing symlink escape")
        current.mkdir(exist_ok=True)
        os.chmod(current, 0o755)


def _write_file(
    tar: tarfile.TarFile,
    member: tarfile.TarInfo,
    dest_root: Path,
    rel: str,
    written: int,
    max_bytes: int | None,
) -> int:
    source = tar.extractfile(member)
    if source is None:
        raise UnsafeArchive("refusing special file")
    parts = rel.split("/")
    current = dest_root
    for part in parts[:-1]:
        current = current / part
        if current.is_symlink():
            raise UnsafeArchive("refusing symlink escape")
        current.mkdir(exist_ok=True)
    target = current / parts[-1]
    if target.is_symlink():
        raise UnsafeArchive("refusing symlink escape")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    fd = os.open(target, flags, 0o644)
    try:
        with os.fdopen(fd, "wb") as handle:
            remaining = member.size
            while remaining > 0:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                written += len(chunk)
                if max_bytes is not None and written > max_bytes:
                    raise DiskLimitExceeded("disk limit exceeded")
                handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        target.unlink(missing_ok=True)
        raise
    os.chmod(target, 0o644)
    return written


def _make_symlink(dest_root: Path, rel: str, linkname: str) -> None:
    if not _symlink_stays_inside(rel, linkname):
        raise UnsafeArchive("refusing symlink escape")
    target = dest_root.joinpath(*rel.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() or target.is_symlink():
        raise UnsafeArchive("refusing symlink escape")
    os.symlink(linkname, target)
    resolved = target.resolve()
    if not resolved.is_relative_to(dest_root):
        target.unlink()
        raise UnsafeArchive("refusing symlink escape")


def _make_hardlink(dest_root: Path, rel: str, linkname: str) -> None:
    try:
        target_rel = _normalize_name(linkname)
    except UnsafeArchive as exc:
        raise UnsafeArchive("refusing hardlink escape") from exc
    source = dest_root.joinpath(*target_rel.split("/"))
    try:
        source_resolved = source.resolve()
    except OSError as exc:
        raise UnsafeArchive("refusing hardlink escape") from exc
    if not source_resolved.is_relative_to(dest_root) or not source_resolved.is_file():
        raise UnsafeArchive("refusing hardlink escape")
    if source.is_symlink():
        raise UnsafeArchive("refusing hardlink escape")
    target = dest_root.joinpath(*rel.split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    os.link(source_resolved, target)
