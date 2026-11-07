"""Content-addressed artifact store. Stdlib only.

Blobs live at ``<artifact_root>/sha256/<64 hex>``. Digests are ``sha256:<hex>``.
Writes land in a temp file in that directory and are renamed into place.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from pathlib import Path

from challenge_runner.protocol import DIGEST_RE

_HEX_RE_LEN = 64


class ArtifactError(ValueError):
    pass


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.blob_dir = self.root / "sha256"
        self.blob_dir.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, data: bytes) -> str:
        if not isinstance(data, (bytes, bytearray)):
            raise ArtifactError("put_bytes expects bytes")
        digest = hashlib.sha256(data).hexdigest()
        self._commit(digest, bytes(data))
        return f"sha256:{digest}"

    def put_file(self, path: Path) -> str:
        source = Path(path)
        tmp = self._temp_path("pending")
        hasher = hashlib.sha256()
        try:
            with source.open("rb") as src, tmp.open("wb") as dst:
                while True:
                    chunk = src.read(1024 * 1024)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    dst.write(chunk)
                dst.flush()
                os.fsync(dst.fileno())
            digest = hasher.hexdigest()
            self._install_temp(digest, tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return f"sha256:{digest}"

    def get_path(self, digest: str) -> Path:
        hexpart = _hex_from_digest(digest)
        path = self.blob_dir / hexpart
        resolved = path.resolve()
        if not resolved.is_relative_to(self.blob_dir.resolve()):
            raise ArtifactError("refusing digest")
        if not resolved.is_file():
            raise FileNotFoundError(digest)
        hasher = hashlib.sha256()
        with resolved.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                hasher.update(chunk)
        if hasher.hexdigest() != hexpart:
            raise ArtifactError("digest mismatch")
        return resolved

    def _commit(self, hexpart: str, data: bytes) -> None:
        tmp = self._temp_path(hexpart)
        try:
            with tmp.open("wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._install_temp(hexpart, tmp)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    def _install_temp(self, hexpart: str, tmp: Path) -> None:
        final = self.blob_dir / hexpart
        if final.exists():
            current = hashlib.sha256(final.read_bytes()).hexdigest()
            tmp.unlink(missing_ok=True)
            if current != hexpart:
                raise ArtifactError("stored blob does not match its name")
            return
        os.replace(tmp, final)
        _fsync_dir(self.blob_dir)

    def _temp_path(self, label: str) -> Path:
        name = f".{label}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        return self.blob_dir / name


def put_bytes(artifact_root: Path, data: bytes) -> str:
    return ArtifactStore(artifact_root).put_bytes(data)


def put_file(artifact_root: Path, path: Path) -> str:
    return ArtifactStore(artifact_root).put_file(path)


def get_path(artifact_root: Path, digest: str) -> Path:
    return ArtifactStore(artifact_root).get_path(digest)


def _hex_from_digest(digest: str) -> str:
    if not isinstance(digest, str) or DIGEST_RE.fullmatch(digest) is None:
        raise ArtifactError("refusing digest")
    hexpart = digest.split(":", 1)[1]
    if len(hexpart) != _HEX_RE_LEN:
        raise ArtifactError("refusing digest")
    return hexpart


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
