"""GitHub App client: app JWT, installation tokens, diffs, check runs, comments."""

from __future__ import annotations


class GitHubAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


async def installation_token(installation_id: int) -> str:
    raise NotImplementedError


async def get_pull_request(installation_id: int, repo: str, pr_number: int) -> dict:
    raise NotImplementedError


async def compare_diff(installation_id: int, repo: str, base_sha: str, head_sha: str) -> str:
    raise NotImplementedError


async def list_check_runs(installation_id: int, repo: str, head_sha: str) -> list[dict]:
    raise NotImplementedError


async def upsert_sticky_comment(
    installation_id: int,
    repo: str,
    issue_number: int,
    marker: str,
    body: str,
    comment_id: int | None = None,
) -> int:
    raise NotImplementedError


async def get_collaborator_permission(installation_id: int, repo: str, username: str) -> str:
    raise NotImplementedError
