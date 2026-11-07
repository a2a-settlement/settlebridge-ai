"""Narrowed, uncached installation tokens for the trusted fetcher (owner: S3).

The token is scoped to one repository with ``{"contents": "read"}`` by sending
``repositories`` and ``permissions`` in the access_tokens request body. It is never
cached, logged, written to disk, or passed into any sandbox.
"""

from __future__ import annotations


async def scoped_read_token(installation_id: int, repo_full_name: str) -> str:
    raise NotImplementedError
