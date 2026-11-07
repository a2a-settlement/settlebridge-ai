"""Narrowed, uncached installation tokens for the trusted fetcher (owner: S3).

The token is scoped to one repository with ``{"contents": "read"}`` by sending
``repositories`` and ``permissions`` in the access_tokens request body. It is never
cached, logged, written to disk, or passed into any sandbox.
"""

from __future__ import annotations

from typing import Any

from app.services.github_app import (
    GitHubAPIError,
    _api_root,
    _app_jwt,
    _client,
    _ensure_ok,
    _read_json,
    _send,
    _validate_repo,
)


async def scoped_read_token(installation_id: int, repo_full_name: str) -> str:
    repo = _validate_repo(repo_full_name)
    name = repo.split("/", 1)[1]
    body = {"repositories": [name], "permissions": {"contents": "read"}}
    url = f"{_api_root()}/app/installations/{installation_id}/access_tokens"
    async with _client() as client:
        response = await _send(client, "POST", url, _app_jwt(), json_body=body)
    _ensure_ok(response)
    return _narrow_token(_read_json(response), response.status_code, repo)


_ALLOWED_PERMISSIONS = (
    {"contents": "read"},
    {"contents": "read", "metadata": "read"},
)


def _narrow_token(data: Any, status_code: int, repo_full_name: str) -> str:
    if not isinstance(data, dict):
        raise GitHubAPIError(
            "GitHub installation token response was incomplete",
            status_code,
        )
    permissions = data.get("permissions")
    selection = data.get("repository_selection")
    # GitHub may add metadata:read to every installation token; nothing else is allowed.
    if permissions not in _ALLOWED_PERMISSIONS or selection != "selected":
        raise GitHubAPIError(
            "GitHub installation token response was not scoped to contents:read "
            "on the selected repository",
            status_code,
        )
    repositories = data.get("repositories")
    if repositories is not None:
        names = (
            [
                item["full_name"].casefold()
                for item in repositories
                if isinstance(item, dict) and isinstance(item.get("full_name"), str)
            ]
            if isinstance(repositories, list)
            else None
        )
        if names is None or len(names) != len(repositories) or names != [repo_full_name.casefold()]:
            raise GitHubAPIError(
                "GitHub installation token response named other repositories",
                status_code,
            )
    token = data.get("token")
    if not isinstance(token, str) or token == "":
        raise GitHubAPIError(
            "GitHub installation token response was incomplete",
            status_code,
        )
    return token
