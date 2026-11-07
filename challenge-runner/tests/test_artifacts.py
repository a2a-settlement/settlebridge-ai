"""Content-addressed artifact store."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from challenge_runner.artifacts import ArtifactError, ArtifactStore, get_path, put_bytes, put_file


def test_put_bytes_addresses_by_sha256_and_get_path_verifies(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    digest = store.put_bytes(b"hello")
    hexpart = hashlib.sha256(b"hello").hexdigest()
    assert digest == f"sha256:{hexpart}"
    blob = tmp_path / "sha256" / hexpart
    assert blob.read_bytes() == b"hello"
    assert store.get_path(digest) == blob.resolve()
    assert get_path(tmp_path, digest).read_bytes() == b"hello"


def test_put_file_matches_put_bytes_and_second_put_is_idempotent(tmp_path: Path):
    source = tmp_path / "payload.bin"
    source.write_bytes(b"same-bytes")
    first = put_file(tmp_path, source)
    second = put_bytes(tmp_path, b"same-bytes")
    assert first == second
    blobs = [path for path in (tmp_path / "sha256").iterdir() if not path.name.startswith(".")]
    assert len(blobs) == 1
    assert not list((tmp_path / "sha256").glob("*.tmp"))


def test_get_path_refuses_bad_digests_and_detects_tampering(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    digest = store.put_bytes(b"intact")
    with pytest.raises(ArtifactError):
        store.get_path("sha256:abcd")
    with pytest.raises(ArtifactError):
        store.get_path("sha256:" + "A" * 64)
    with pytest.raises(ArtifactError):
        store.get_path("sha1:" + "ab" * 32)
    with pytest.raises(ArtifactError):
        store.get_path("../" + "ab" * 32)
    blob = store.get_path(digest)
    blob.write_bytes(b"tampered")
    with pytest.raises(ArtifactError, match="mismatch"):
        store.get_path(digest)


def test_get_path_missing_blob_raises(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    missing = "sha256:" + "cd" * 32
    with pytest.raises(FileNotFoundError):
        store.get_path(missing)


def test_put_replaces_via_tempfile_in_the_blob_directory(tmp_path: Path, monkeypatch):
    store = ArtifactStore(tmp_path)
    seen: list[Path] = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append(Path(src))
        assert Path(src).parent == store.blob_dir
        assert Path(dst).parent == store.blob_dir
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    store.put_bytes(b"atomic")
    assert seen and not seen[0].exists()
