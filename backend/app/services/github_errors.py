"""Job outcome signals raised by GitHub job handlers."""

from __future__ import annotations

from datetime import datetime

from app.services.github_app import GitHubAPIError


class RetryableJobError(Exception):
    """Transient failure; the job is retried with backoff."""


class PermanentJobError(Exception):
    """The job can never succeed; it is marked failed without retry."""


class GitHubRateLimited(GitHubAPIError):
    """GitHub asked the caller to wait until ``retry_at`` before trying again."""

    def __init__(self, message: str, *, retry_at: datetime, status_code: int | None = None):
        super().__init__(message, status_code=status_code)
        self.retry_at = retry_at
