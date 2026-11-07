"""GitHub rate-limit errors. No network — httpx.MockTransport only."""

from __future__ import annotations

import asyncio
import json
import sys
import types
import uuid
from datetime import datetime, timedelta, timezone

import httpx
import pytest

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from app.config import settings  # noqa: E402
from app.services import github_app, github_jobs  # noqa: E402
from app.services.github_app import GitHubAPIError  # noqa: E402
from app.services.github_errors import GitHubRateLimited  # noqa: E402
from app.services.github_jobs import compute_backoff, run_job  # noqa: E402

FROZEN = datetime(2026, 11, 7, 16, 0, tzinfo=timezone.utc)
API = "https://api.github.com"


def _run(coro):
    return asyncio.run(coro)


def _epoch(delta: timedelta) -> str:
    return str(int((FROZEN + delta).timestamp()))


@pytest.fixture(autouse=True)
def _freeze_now(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(github_app, "_now", lambda: FROZEN)


async def _send(status: int, headers: dict[str, str] | None = None, *, body: dict | None = None):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        kwargs = {"headers": headers or {}}
        if body is not None:
            kwargs["json"] = body
        return httpx.Response(status, **kwargs)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as client:
        response = await github_app._send(
            client,
            "POST" if body is not None else "GET",
            f"{API}/rate_limit",
            "ghs_token",
            json_body=body,
        )
    return response, seen


def test_rate_limited_is_a_github_api_error():
    exc = GitHubRateLimited("limited", retry_at=FROZEN, status_code=429)
    assert isinstance(exc, GitHubAPIError)
    assert exc.status_code == 429
    assert exc.retry_at == FROZEN


@pytest.mark.parametrize(
    ("status", "headers", "delta"),
    [
        (429, {}, timedelta(seconds=60)),
        (429, {"Retry-After": "30"}, timedelta(seconds=30)),
        (429, {"retry-after": "7200"}, timedelta(hours=1)),
        (429, {"x-ratelimit-reset": "epoch:20m"}, timedelta(minutes=20)),
        (429, {"x-ratelimit-reset": "epoch:2h"}, timedelta(hours=1)),
        (
            403,
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "epoch:2m"},
            timedelta(minutes=2),
        ),
        (403, {"x-ratelimit-remaining": "0"}, timedelta(seconds=60)),
        (
            429,
            {"retry-after": "10", "x-ratelimit-reset": "epoch:30m"},
            timedelta(seconds=10),
        ),
        (
            403,
            {"retry-after": "12", "x-ratelimit-remaining": "0", "x-ratelimit-reset": "epoch:30m"},
            timedelta(seconds=12),
        ),
        (429, {"retry-after": "soon", "x-ratelimit-reset": "epoch:5m"}, timedelta(minutes=5)),
        (429, {"retry-after": "soon"}, timedelta(seconds=60)),
    ],
)
def test_send_raises_rate_limit_from_headers(status, headers, delta):
    built = {}
    for name, value in headers.items():
        if value.startswith("epoch:"):
            amount = value.removeprefix("epoch:")
            unit = amount[-1]
            count = int(amount[:-1])
            built[name] = _epoch(
                timedelta(minutes=count) if unit == "m" else timedelta(hours=count)
            )
        else:
            built[name] = value
    with pytest.raises(GitHubRateLimited) as caught:
        _run(_send(status, built))
    exc = caught.value
    assert exc.retry_at == FROZEN + delta
    assert exc.status_code == status
    assert isinstance(exc, GitHubAPIError)
    assert str(status) in str(exc)
    assert "/rate_limit" in str(exc)


@pytest.mark.parametrize("status", [403, 429])
def test_bounty_handlers_pass_rate_limits_through(status):
    from app.services.github_bounty_service import _reraise_external

    exc = GitHubRateLimited("limited", retry_at=FROZEN, status_code=status)
    with pytest.raises(GitHubRateLimited) as caught:
        _reraise_external(exc)
    assert caught.value is exc


def test_send_does_not_retry_a_rate_limit():
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(429, headers={"retry-after": "5"})

    transport = httpx.MockTransport(handler)

    async def once():
        async with httpx.AsyncClient(transport=transport) as client:
            await github_app._send(client, "GET", f"{API}/rate_limit", "ghs_token")

    with pytest.raises(GitHubRateLimited):
        _run(once())
    assert len(seen) == 1


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (200, {}),
        (200, {"retry-after": "15"}),
        (403, {}),
        (403, {"x-ratelimit-remaining": "1"}),
        (404, {"x-ratelimit-remaining": "0"}),
        (500, {}),
    ],
)
def test_send_returns_other_responses(status, headers):
    response, seen = _run(_send(status, headers, body={"title": "t"} if status == 200 else None))
    assert response.status_code == status
    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer ghs_token"
    if status == 200:
        assert json.loads(seen[0].content) == {"title": "t"}
        assert response.json() == {"title": "t"}


class _MemSession:
    def __init__(self, store: dict):
        self.store = store
        self.commits = 0
        self.rollbacks = 0

    async def get(self, model, pk):
        return self.store.get(pk)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _Factory:
    def __init__(self, store: dict):
        self.store = store
        self.sessions: list[_MemSession] = []

    def __call__(self):
        session = _MemSession(self.store)
        self.sessions.append(session)
        return session


def _job(**overrides):
    job = type("Job", (), {})()
    job.id = overrides.get("id", uuid.uuid4())
    job.kind = overrides.get("kind", "issue_labeled")
    job.status = overrides.get("status", "running")
    job.attempts = overrides.get("attempts", 1)
    job.locked_until = overrides.get("locked_until", datetime.now(timezone.utc))
    job.last_error = overrides.get("last_error", None)
    job.next_run_at = overrides.get("next_run_at", datetime(2020, 1, 1, tzinfo=timezone.utc))
    return job


def test_rate_limit_schedules_retry_at_when_it_is_later_than_backoff(monkeypatch):
    retry_at = datetime.now(timezone.utc) + timedelta(hours=2)
    job = _job(attempts=1, status="running")

    async def handler(db, current):
        raise GitHubRateLimited(
            "GitHub API 429 for GET /rate_limit", retry_at=retry_at, status_code=429
        )

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    _run(run_job(_Factory({job.id: job}), job.id))
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.locked_until is None
    assert job.next_run_at == retry_at
    assert "429" in job.last_error


def test_rate_limit_keeps_backoff_when_it_is_later_than_retry_at(monkeypatch):
    retry_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    job = _job(attempts=5, status="running")

    async def handler(db, current):
        raise GitHubRateLimited("limited", retry_at=retry_at, status_code=429)

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    before = datetime.now(timezone.utc)
    _run(run_job(_Factory({job.id: job}), job.id))
    delay = compute_backoff(4)
    assert job.status == "pending"
    assert job.attempts == 4
    assert job.next_run_at >= before + delay
    assert job.next_run_at <= datetime.now(timezone.utc) + delay
    assert job.next_run_at > retry_at


def test_rate_limit_accepts_a_naive_retry_at(monkeypatch):
    naive = (datetime.now(timezone.utc) + timedelta(hours=3)).replace(tzinfo=None)
    job = _job(attempts=2, status="running")

    async def handler(db, current):
        raise GitHubRateLimited("limited", retry_at=naive, status_code=403)

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    _run(run_job(_Factory({job.id: job}), job.id))
    assert job.status == "pending"
    assert job.attempts == 1
    assert job.next_run_at == naive.replace(tzinfo=timezone.utc)


def test_rate_limits_never_reach_dead(monkeypatch):
    retry_at = datetime.now(timezone.utc) + timedelta(hours=2)
    job = _job(attempts=0, status="running")

    async def handler(db, current):
        raise GitHubRateLimited("limited", retry_at=retry_at, status_code=429)

    monkeypatch.setattr(github_jobs, "handle_job", handler)
    factory = _Factory({job.id: job})
    for _ in range(settings.GITHUB_JOB_MAX_ATTEMPTS + 12):
        job.attempts += 1
        job.status = "running"
        job.locked_until = datetime.now(timezone.utc)
        _run(run_job(factory, job.id))
        assert job.status == "pending"
        assert job.attempts == 0
        assert job.locked_until is None
        assert job.next_run_at == retry_at
    assert job.status != "dead"
