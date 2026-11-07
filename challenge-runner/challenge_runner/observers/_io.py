"""Stdlib capture reads for verifier observers.

A missing file, a symlink, a non-regular file, a file larger than 1 MiB, more
than 4096 JSON objects, or a file that is not UTF-8 JSON objects yields no
records. One syntactically bad line drops the file so a partial write cannot
confirm a claim. ``O_NOFOLLOW`` keeps a symlink from being treated as a capture.
"""

from __future__ import annotations

import json
import os
import stat

MAX_BYTES = 1_048_576
MAX_LINES = 4096
MAX_EVENTS = 32


def load_jsonl(capture_dir: str, name: str) -> list[dict]:
    if not isinstance(capture_dir, str) or not isinstance(name, str):
        return []
    if not name or name != os.path.basename(name) or name in {".", ".."}:
        return []
    path = os.path.join(capture_dir, name)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        fd = os.open(path, flags)
    except OSError:
        return []
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_BYTES:
            return []
        blob = os.read(fd, MAX_BYTES + 1)
    except OSError:
        return []
    finally:
        os.close(fd)
    if len(blob) > MAX_BYTES:
        return []
    try:
        text = blob.decode("utf-8")
    except UnicodeDecodeError:
        return []
    records: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if len(records) >= MAX_LINES:
            return []
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(item, dict):
            return []
        records.append(item)
    return records


def clip(value: str, limit: int = 180) -> str:
    text = " ".join(value.split())
    if len(text) > limit:
        return text[:limit]
    return text
