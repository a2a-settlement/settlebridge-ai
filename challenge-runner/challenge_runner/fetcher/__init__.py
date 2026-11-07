"""Trusted fetcher (owner: S3). Runs outside every sandbox.

Clones with a narrowed token supplied by the caller, exports a source tarball with
``git archive`` at the pinned SHA (plus, when asked, a history bundle with no remote
configuration), scrubs ``.git/config`` and any extraheader, and discards the token.
The token never reaches a sandbox, an environment variable of a child sandbox, a
mount, a log, or the returned values.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


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
    raise NotImplementedError
