"""Webhook route records a delivery and enqueues at most one job, in one transaction."""

# ruff: noqa: E402

import hashlib
import hmac
import json
import sys
import types

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import get_db
from app.models.github import GitHubJob, GitHubWebhookDelivery
from app.routes.github import router

SECRET = "test-webhook-secret"


class FakeSession:
    def __init__(self, fail_commit: bool = False):
        self.added: list[object] = []
        self.events: list[str] = []
        self.fail_commit = fail_commit
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)
        self.events.append("add")

    async def commit(self) -> None:
        self.commits += 1
        self.events.append("commit")
        if self.fail_commit:
            raise IntegrityError("INSERT", {}, Exception("duplicate key"))

    async def rollback(self) -> None:
        self.rollbacks += 1
        self.events.append("rollback")


@pytest.fixture(autouse=True)
def _webhook_secret(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", SECRET)


def _sign(body: bytes, secret: str = SECRET) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def _client(session: FakeSession) -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/github")

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def _post(
    client: TestClient,
    event: str | None,
    delivery: str | None,
    payload: object,
    *,
    signature: str | None = None,
    raw_body: bytes | None = None,
):
    body = raw_body if raw_body is not None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if event is not None:
        headers["X-GitHub-Event"] = event
    if delivery is not None:
        headers["X-GitHub-Delivery"] = delivery
    if signature is not None:
        headers["X-Hub-Signature-256"] = signature
    else:
        headers["X-Hub-Signature-256"] = _sign(body)
    return client.post("/api/v1/github/webhook", content=body, headers=headers)


def _jobs(session: FakeSession) -> list[GitHubJob]:
    return [obj for obj in session.added if isinstance(obj, GitHubJob)]


def _deliveries(session: FakeSession) -> list[GitHubWebhookDelivery]:
    return [obj for obj in session.added if isinstance(obj, GitHubWebhookDelivery)]


def test_bad_signature_is_401():
    session = FakeSession()
    with _client(session) as client:
        response = _post(
            client,
            "issues",
            "deliv-bad",
            {"action": "labeled"},
            signature="sha256=" + "ab" * 32,
        )
    assert response.status_code == 401
    assert session.added == []
    assert session.commits == 0


def test_missing_signature_is_401():
    session = FakeSession()
    body = b'{"action":"labeled"}'
    headers = {
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "deliv-nosig",
        "Content-Type": "application/json",
    }
    with _client(session) as client:
        response = client.post("/api/v1/github/webhook", content=body, headers=headers)
    assert response.status_code == 401
    assert session.added == []


def test_empty_secret_is_401(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_SECRET", "")
    session = FakeSession()
    body = b'{"zen":"hi"}'
    headers = {
        "X-GitHub-Event": "ping",
        "X-GitHub-Delivery": "deliv-empty-secret",
        "X-Hub-Signature-256": _sign(body, secret=""),
        "Content-Type": "application/json",
    }
    with _client(session) as client:
        response = client.post("/api/v1/github/webhook", content=body, headers=headers)
    assert response.status_code == 401
    assert session.added == []


@pytest.mark.parametrize(
    ("event", "delivery"),
    [
        (None, "deliv-1"),
        ("issues", None),
        (None, None),
        ("", "deliv-1"),
        ("issues", ""),
    ],
)
def test_missing_headers_are_400(event, delivery):
    session = FakeSession()
    with _client(session) as client:
        response = _post(client, event, delivery, {"action": "labeled"})
    assert response.status_code == 400
    assert session.added == []
    assert session.commits == 0


def test_invalid_json_is_400():
    session = FakeSession()
    with _client(session) as client:
        response = _post(
            client,
            "issues",
            "deliv-json",
            None,
            raw_body=b"{not-json",
        )
    assert response.status_code == 400
    assert session.added == []


def test_ping_returns_pong_without_writing():
    session = FakeSession()
    with _client(session) as client:
        response = _post(client, "ping", "deliv-ping", {"zen": "keep it logically awesome"})
    assert response.status_code == 200
    assert response.json() == {"status": "pong"}
    assert session.added == []
    assert session.commits == 0
    assert session.rollbacks == 0


def test_ping_rejects_bad_signature():
    session = FakeSession()
    with _client(session) as client:
        response = _post(
            client,
            "ping",
            "deliv-ping",
            {"zen": "nope"},
            signature="sha256=" + "00" * 32,
        )
    assert response.status_code == 401
    assert session.added == []


def test_issues_labeled_queues_delivery_and_job():
    session = FakeSession()
    payload = {"action": "labeled", "issue": {"number": 7}, "label": {"name": "bounty"}}
    with _client(session) as client:
        response = _post(client, "issues", "deliv-labeled", payload)
    assert response.status_code == 202
    assert response.json() == {"status": "queued", "delivery_id": "deliv-labeled"}
    assert session.events == ["add", "add", "commit"]
    assert session.rollbacks == 0
    deliveries = _deliveries(session)
    jobs = _jobs(session)
    assert len(deliveries) == 1
    assert len(jobs) == 1
    delivery = deliveries[0]
    assert delivery.delivery_id == "deliv-labeled"
    assert delivery.event == "issues"
    assert delivery.action == "labeled"
    job = jobs[0]
    assert job.kind == "issue_labeled"
    assert job.status == "pending"
    assert job.delivery_id == "deliv-labeled"
    assert job.payload == {"event": "issues", "action": "labeled", "data": payload}


def test_unmapped_event_records_delivery_only():
    session = FakeSession()
    payload = {"ref": "refs/heads/main"}
    with _client(session) as client:
        response = _post(client, "push", "deliv-push", payload)
    assert response.status_code == 202
    assert response.json() == {"status": "ignored", "delivery_id": "deliv-push"}
    assert session.events == ["add", "commit"]
    assert _jobs(session) == []
    deliveries = _deliveries(session)
    assert len(deliveries) == 1
    assert deliveries[0].event == "push"
    assert deliveries[0].delivery_id == "deliv-push"
    assert deliveries[0].action is None


def test_duplicate_delivery_rolls_back():
    session = FakeSession(fail_commit=True)
    with _client(session) as client:
        response = _post(
            client,
            "issues",
            "deliv-dup",
            {"action": "labeled"},
        )
    assert response.status_code == 200
    assert response.json() == {"status": "duplicate"}
    assert session.events == ["add", "add", "commit", "rollback"]
    assert session.rollbacks == 1


@pytest.mark.parametrize(
    ("event", "payload", "kind"),
    [
        ("installation", {"action": "created"}, "installation"),
        ("installation", {"action": "deleted"}, "installation"),
        ("installation", {}, "installation"),
        ("installation_repositories", {"action": "added"}, "installation"),
        ("installation_repositories", {"action": "removed"}, "installation"),
        ("issues", {"action": "labeled"}, "issue_labeled"),
        ("issues", {"action": "unlabeled"}, "issue_changed"),
        ("issues", {"action": "edited"}, "issue_changed"),
        ("issues", {"action": "reopened"}, "issue_changed"),
        ("issues", {"action": "closed"}, "issue_closed"),
        ("issues", {"action": "opened"}, None),
        ("pull_request", {"action": "opened"}, "pr_updated"),
        ("pull_request", {"action": "synchronize"}, "pr_updated"),
        ("pull_request", {"action": "reopened"}, "pr_updated"),
        ("pull_request", {"action": "edited"}, "pr_updated"),
        ("pull_request", {"action": "closed"}, "pr_closed"),
        ("pull_request", {"action": "assigned"}, None),
        ("check_suite", {"action": "completed"}, "checks_completed"),
        ("check_suite", {"action": "requested"}, None),
        (
            "issue_comment",
            {"action": "created", "comment": {"body": "/settlebridge clear"}},
            "review_clear_comment",
        ),
        (
            "issue_comment",
            {"action": "created", "comment": {"body": "  /settlebridge clear\nthanks"}},
            "review_clear_comment",
        ),
        (
            "issue_comment",
            {"action": "created", "comment": {"body": "please /settlebridge clear"}},
            None,
        ),
        (
            "issue_comment",
            {"action": "created", "comment": {"body": "hello"}},
            None,
        ),
        (
            "issue_comment",
            {"action": "edited", "comment": {"body": "/settlebridge clear"}},
            None,
        ),
        ("push", {"after": "abc"}, None),
    ],
)
def test_event_action_mapping(event, payload, kind):
    session = FakeSession()
    with _client(session) as client:
        response = _post(client, event, f"deliv-{event}", payload)
    assert response.status_code == 202
    jobs = _jobs(session)
    if kind is None:
        assert response.json()["status"] == "ignored"
        assert jobs == []
        assert len(_deliveries(session)) == 1
    else:
        assert response.json()["status"] == "queued"
        assert len(jobs) == 1
        assert jobs[0].kind == kind
        assert jobs[0].payload["event"] == event
        assert jobs[0].payload["action"] == payload.get("action")
        assert jobs[0].payload["data"] == payload


def test_content_length_over_limit_is_413_before_signature(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_MAX_BODY_BYTES", 64)
    session = FakeSession()
    body = b'{"zen":"hi"}'
    headers = {
        "X-GitHub-Event": "ping",
        "X-GitHub-Delivery": "deliv-length",
        "X-Hub-Signature-256": "sha256=" + "ab" * 32,
        "Content-Type": "application/json",
    }
    with _client(session) as client:
        request = client.build_request(
            "POST", "/api/v1/github/webhook", content=body, headers=headers
        )
        request.headers["content-length"] = "1000000"
        response = client.send(request)
    assert response.status_code == 413
    assert response.json()["detail"] == "Webhook body too large"
    assert session.added == []
    assert session.commits == 0


def test_streamed_body_over_limit_is_413_before_signature(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_MAX_BODY_BYTES", 16)
    session = FakeSession()

    def chunks():
        yield b"x" * 10
        yield b"y" * 10

    headers = {
        "X-GitHub-Event": "ping",
        "X-GitHub-Delivery": "deliv-stream",
        "X-Hub-Signature-256": "sha256=" + "cd" * 32,
        "Content-Type": "application/json",
    }
    with _client(session) as client:
        response = client.post("/api/v1/github/webhook", content=chunks(), headers=headers)
    assert response.status_code == 413
    assert session.added == []
    assert session.commits == 0


def test_under_limit_content_length_still_caps_the_stream(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_MAX_BODY_BYTES", 24)
    session = FakeSession()
    body = b"z" * 40
    headers = {
        "X-GitHub-Event": "ping",
        "X-GitHub-Delivery": "deliv-lie",
        "X-Hub-Signature-256": _sign(body),
        "Content-Type": "application/json",
    }
    with _client(session) as client:
        request = client.build_request(
            "POST", "/api/v1/github/webhook", content=body, headers=headers
        )
        request.headers["content-length"] = "8"
        response = client.send(request)
    assert response.status_code == 413
    assert session.added == []
    assert session.commits == 0


def test_chunked_body_under_limit_is_reassembled_for_hmac(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_WEBHOOK_MAX_BODY_BYTES", 1024)
    session = FakeSession()
    body = b'{"zen":"keep it logically awesome"}'

    def chunks():
        yield body[:5]
        yield body[5:]

    headers = {
        "X-GitHub-Event": "ping",
        "X-GitHub-Delivery": "deliv-chunk-ok",
        "X-Hub-Signature-256": _sign(body),
        "Content-Type": "application/json",
    }
    with _client(session) as client:
        response = client.post("/api/v1/github/webhook", content=chunks(), headers=headers)
    assert response.status_code == 200
    assert response.json() == {"status": "pong"}
    assert session.added == []
