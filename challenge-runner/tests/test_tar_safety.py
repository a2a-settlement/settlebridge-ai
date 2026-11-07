"""Unsafe tar members never leave the extraction root."""

from __future__ import annotations

import io
import stat
import tarfile
from pathlib import Path

import pytest

from challenge_runner.sandbox.tarutil import UnsafeArchive, safe_extract


def _tar(members: list[tuple[tarfile.TarInfo, bytes | None]]) -> Path:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for info, payload in members:
            archive.addfile(info, io.BytesIO(payload) if payload is not None else None)
    return buffer


def _file(name: str, data: bytes, mode: int = 0o644) -> tuple[tarfile.TarInfo, bytes]:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.type = tarfile.REGTYPE
    return info, data


def _save(tmp_path: Path, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> Path:
    path = tmp_path / "archive.tar"
    raw = _tar(members)
    path.write_bytes(raw.getvalue())
    return path


def test_extracts_regular_files_and_clears_setuid(tmp_path: Path):
    archive = _save(tmp_path, [_file("pkg/mod.py", b"print(1)\n", mode=0o4755)])
    dest = tmp_path / "out"
    safe_extract(archive, dest)
    extracted = dest / "pkg" / "mod.py"
    assert extracted.read_bytes() == b"print(1)\n"
    assert stat.S_IMODE(extracted.stat().st_mode) == 0o644


def test_rejects_absolute_parent_and_nested_parent_paths(tmp_path: Path):
    dest = tmp_path / "out"
    absolute = _save(tmp_path, [_file("/etc/passwd", b"x")])
    with pytest.raises(UnsafeArchive, match="absolute"):
        safe_extract(absolute, dest)
    nested = _save(tmp_path, [_file("foo/../../etc/passwd", b"x")])
    with pytest.raises(UnsafeArchive, match="parent"):
        safe_extract(nested, dest)
    assert not (dest / "etc" / "passwd").exists()
    assert not list(dest.rglob("passwd"))


def test_rejects_symlink_and_hardlink_escapes(tmp_path: Path):
    dest = tmp_path / "out"
    link = tarfile.TarInfo("alias")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc/passwd"
    with pytest.raises(UnsafeArchive, match="symlink"):
        safe_extract(_save(tmp_path, [(link, None)]), dest)
    outside = tarfile.TarInfo("sub/link")
    outside.type = tarfile.SYMTYPE
    outside.linkname = "../../outside"
    with pytest.raises(UnsafeArchive, match="symlink"):
        safe_extract(_save(tmp_path, [(outside, None)]), dest)
    hard = tarfile.TarInfo("hard")
    hard.type = tarfile.LNKTYPE
    hard.linkname = "../secret"
    with pytest.raises(UnsafeArchive, match="hardlink"):
        safe_extract(_save(tmp_path, [(hard, None)]), dest)
    assert not any(path.name == "outside" for path in dest.rglob("*"))


def test_allows_internal_symlink_and_hardlink(tmp_path: Path):
    link = tarfile.TarInfo("sub/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "target.txt"
    hard = tarfile.TarInfo("copy.txt")
    hard.type = tarfile.LNKTYPE
    hard.linkname = "pkg.txt"
    archive = _save(
        tmp_path,
        [
            _file("sub/target.txt", b"inside"),
            (link, None),
            _file("pkg.txt", b"hard"),
            (hard, None),
        ],
    )
    dest = tmp_path / "out"
    safe_extract(archive, dest)
    assert (dest / "sub" / "link").is_symlink()
    assert (dest / "sub" / "link").resolve().is_relative_to(dest.resolve())
    assert (dest / "copy.txt").read_bytes() == b"hard"


def test_rejects_device_and_fifo(tmp_path: Path):
    dest = tmp_path / "out"
    device = tarfile.TarInfo("dev/zero")
    device.type = tarfile.CHRTYPE
    device.devmajor = 1
    device.devminor = 5
    with pytest.raises(UnsafeArchive, match="device"):
        safe_extract(_save(tmp_path, [(device, None)]), dest)
    fifo = tarfile.TarInfo("queue")
    fifo.type = tarfile.FIFOTYPE
    with pytest.raises(UnsafeArchive, match="special"):
        safe_extract(_save(tmp_path, [(fifo, None)]), dest)


def test_challenger_bundle_must_stay_under_challenge_tests(tmp_path: Path):
    dest = tmp_path / "work"
    good = _save(tmp_path, [_file("challenge_tests/test_ok.py", b"def test_ok():\n    pass\n")])
    safe_extract(good, dest, required_prefix="challenge_tests")
    assert (dest / "challenge_tests" / "test_ok.py").is_file()

    outside = _save(tmp_path, [_file("setup.py", b"print('no')\n")])
    with pytest.raises(UnsafeArchive, match="challenge_tests"):
        safe_extract(outside, dest, required_prefix="challenge_tests")
    assert not (dest / "setup.py").exists()

    escaped = tarfile.TarInfo("challenge_tests/link")
    escaped.type = tarfile.SYMTYPE
    escaped.linkname = "../secret"
    archive = _save(tmp_path, [(escaped, None), _file("secret", b"nope")])
    try:
        safe_extract(archive, dest, required_prefix="challenge_tests")
    except UnsafeArchive as exc:
        assert "challenge_tests" in str(exc)
    else:
        raise AssertionError("challenger symlink was extracted")
    linked = dest / "challenge_tests" / "link"
    assert not linked.exists()
