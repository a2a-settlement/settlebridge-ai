"""Challenge HTTP routes. In-memory SQLite, no network, create_task is patched."""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.challenge import ChallengeTask
from app.models.github import GitHubRepoLink
from app.routes.challenges import router
from app.services.challenge_report import verify_report
from tests.test_challenge_report import (
    BARE,
    BASE,
    OPERATOR,
    OWNER,
    PROPERTY,
    SECRET_MARKERS,
    STRANGER,
    TARGET,
    TASK,
    Bridge,
    _engine,
    _keys,
    seed,
)

URL = "/api/v1/challenges"
KEY = "k" * 128


def _body(repo: str = "acme/widget") -> dict:
    return {
        "repo_full_name": repo,
        "pr_number": 7,
        "base_sha": BASE,
        "target_sha": TARGET,
        "property_text": PROPERTY,
        "failure_classes": ["integrity"],
        "budget_cents": 500,
        "deadline_at": "2026-12-01T00:00:00Z",
    }


def _tasks(bridge: Bridge) -> list[ChallengeTask]:
    return list(bridge.sync.execute(select(ChallengeTask)).scalars().all())


@pytest.fixture()
def world(monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", False)
    monkeypatch.setattr(settings, "CHALLENGE_OPERATOR_USER_IDS", str(OPERATOR))
    monkeypatch.setattr(settings, "CHALLENGE_REPORT_SIGNING_KEY", "")
    eng = _engine()
    GitHubRepoLink.__table__.create(eng)
    bridge = Bridge(Session(eng, expire_on_commit=False))
    seed(bridge)
    now = datetime(2026, 4, 1, tzinfo=timezone.utc)
    bridge.sync.add(
        GitHubRepoLink(
            id=uuid.uuid4(),
            installation_id=42,
            repo_full_name="acme/widget",
            enabled=True,
            default_policy={},
            required_checks=[],
            auto_dispatch_alerts=False,
            auto_merge_acceptable=False,
            created_at=now,
        )
    )
    bridge.sync.commit()
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    async def override_db():
        yield bridge

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=OPERATOR)
    client = TestClient(app)
    try:
        yield {"client": client, "app": app, "bridge": bridge, "calls": []}
    finally:
        client.close()
        bridge.sync.close()
        eng.dispose()


def _as(world, user_id):
    world["app"].dependency_overrides[get_current_user] = lambda user_id=user_id: SimpleNamespace(
        id=user_id
    )


def _patch_create(world, monkeypatch):
    calls = world["calls"]

    async def fake(db, *, owner_user_id, installation_id, body, idempotency_key):
        calls.append(
            {
                "owner_user_id": owner_user_id,
                "installation_id": installation_id,
                "body": body,
                "idempotency_key": idempotency_key,
            }
        )
        found = (
            await db.execute(
                select(ChallengeTask).where(ChallengeTask.idempotency_key == idempotency_key)
            )
        ).scalar_one_or_none()
        if found is not None:
            return found
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        task = ChallengeTask(
            id=uuid.uuid4(),
            mode="open_pr",
            owner_user_id=owner_user_id,
            installation_id=installation_id,
            repo_full_name=body.repo_full_name,
            pr_number=body.pr_number,
            base_sha=body.base_sha,
            target_sha=body.target_sha,
            property_text=body.property_text,
            failure_classes=list(body.failure_classes),
            environment={},
            budget_cents=body.budget_cents,
            deadline_at=body.deadline_at,
            state="draft",
            state_reason=None,
            idempotency_key=idempotency_key,
            created_at=now,
            updated_at=now,
        )
        db.sync.add(task)
        await db.commit()
        return task

    monkeypatch.setattr("app.services.challenge_orchestrator.create_task", fake)


def _post(world, *, key: str | None = KEY, repo: str = "acme/widget"):
    headers = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    return world["client"].post(URL, json=_body(repo), headers=headers)


def test_get_visibility_and_same_404_body(world):
    _as(world, OWNER)
    owned = world["client"].get(f"{URL}/{TASK}")
    assert owned.status_code == 200, owned.text
    assert owned.json()["id"] == str(TASK)
    assert owned.json()["property_text"] == PROPERTY
    for marker in SECRET_MARKERS:
        assert marker not in owned.text
    assert "secure" not in owned.text.lower()

    _as(world, OPERATOR)
    operated = world["client"].get(f"{URL}/{TASK}")
    assert operated.status_code == 200
    assert operated.json()["id"] == str(TASK)

    _as(world, STRANGER)
    hidden = world["client"].get(f"{URL}/{TASK}")
    missing = world["client"].get(f"{URL}/{uuid.uuid4()}")
    assert hidden.status_code == 404
    assert missing.status_code == 404
    assert hidden.json() == missing.json() == {"error": "not_found"}
    assert PROPERTY not in hidden.text
    assert str(TASK) not in hidden.text


def test_get_report_round_trip_and_404s(world, monkeypatch):
    private, public = _keys()
    monkeypatch.setattr(settings, "CHALLENGE_REPORT_SIGNING_KEY", private)
    _as(world, OWNER)
    response = world["client"].get(f"{URL}/{TASK}/report")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/jose")
    assert "BEGIN PRIVATE KEY" not in response.text
    report = verify_report(response.text, public)
    text = report.model_dump_json()
    assert report.task_id == str(TASK)
    assert "secure" not in text.lower()
    for marker in SECRET_MARKERS:
        assert marker not in text
    payload = json.loads(_b64(response.text.split(".")[1]))
    assert payload["contract_hash"]
    assert "secret-raw-log" not in json.dumps(payload)

    bare = world["client"].get(f"{URL}/{BARE}/report")
    _as(world, STRANGER)
    hidden = world["client"].get(f"{URL}/{TASK}/report")
    missing = world["client"].get(f"{URL}/{uuid.uuid4()}/report")
    _as(world, OWNER)
    monkeypatch.setattr(settings, "CHALLENGE_REPORT_SIGNING_KEY", "")
    unsigned = world["client"].get(f"{URL}/{TASK}/report")
    monkeypatch.setattr(settings, "CHALLENGE_REPORT_SIGNING_KEY", "secret-signing-key-material")
    invalid_key = world["client"].get(f"{URL}/{TASK}/report")
    assert hidden.json() == missing.json() == bare.json() == unsigned.json() == invalid_key.json()
    assert hidden.status_code == 404
    assert "secret-signing-key-material" not in invalid_key.text
    assert PROPERTY not in hidden.text


def test_anonymous_is_401(world, monkeypatch):
    _patch_create(world, monkeypatch)
    world["app"].dependency_overrides.pop(get_current_user)
    client = world["client"]
    assert client.get(f"{URL}/{TASK}").status_code == 401
    assert client.get(f"{URL}/{TASK}/report").status_code == 401
    posted = client.post(URL, json=_body(), headers={"Idempotency-Key": KEY})
    assert posted.status_code == 401
    assert world["calls"] == []


def test_post_disabled_is_503_before_other_checks(world, monkeypatch):
    _patch_create(world, monkeypatch)
    before = len(_tasks(world["bridge"]))
    _as(world, STRANGER)
    response = world["client"].post(URL, json=_body())
    assert response.status_code == 503
    assert response.json() == {"error": "challenge_engine_disabled"}
    assert world["calls"] == []
    assert len(_tasks(world["bridge"])) == before


def test_post_forbidden_before_idempotency_key(world, monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", True)
    _patch_create(world, monkeypatch)
    _as(world, OWNER)
    response = world["client"].post(URL, json=_body())
    assert response.status_code == 403
    assert response.json() == {"error": "forbidden"}
    assert world["calls"] == []


@pytest.mark.parametrize("key", ["", "x" * 129, "bad\nkey", "bad\x7fkey"])
def test_post_bad_idempotency_key_is_400(world, monkeypatch, key):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", True)
    _patch_create(world, monkeypatch)
    _as(world, OPERATOR)
    before = len(_tasks(world["bridge"]))
    response = _post(world, key=key)
    assert response.status_code == 400, response.text
    assert response.json() == {"error": "invalid_idempotency_key"}
    if key:
        assert key not in response.text
    assert world["calls"] == []
    assert len(_tasks(world["bridge"])) == before


def test_non_ascii_idempotency_key_is_rejected():
    from app.routes.challenges import _idempotency_key

    assert _idempotency_key("caf\u00e9") is None
    assert _idempotency_key("k" * 128) == "k" * 128
    assert _idempotency_key("k" * 129) is None


def test_post_missing_idempotency_key_is_400(world, monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", True)
    _patch_create(world, monkeypatch)
    _as(world, OPERATOR)
    response = world["client"].post(URL, json=_body())
    assert response.status_code == 400
    assert response.json() == {"error": "invalid_idempotency_key"}
    assert world["calls"] == []


def test_post_unknown_repo_is_404(world, monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", True)
    _patch_create(world, monkeypatch)
    _as(world, OPERATOR)
    response = _post(world, repo="missing/repo")
    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}
    assert world["calls"] == []


def test_post_ok_and_replay(world, monkeypatch):
    monkeypatch.setattr(settings, "CHALLENGE_ENGINE_ENABLED", True)
    _patch_create(world, monkeypatch)
    _as(world, OPERATOR)
    before = len(_tasks(world["bridge"]))
    created = _post(world)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["state"] == "draft"
    assert body["repo"] == "acme/widget"
    assert body["report_url"] is None
    assert KEY not in created.text
    assert "installation_id" not in created.text
    assert world["calls"][0]["owner_user_id"] == OPERATOR
    assert world["calls"][0]["installation_id"] == 42
    assert world["calls"][0]["idempotency_key"] == KEY
    assert world["calls"][0]["body"].property_text == PROPERTY
    assert len(_tasks(world["bridge"])) == before + 1

    replay = _post(world)
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == body["id"]
    assert len(world["calls"]) == 2
    assert len(_tasks(world["bridge"])) == before + 1

    fetched = world["client"].get(f"{URL}/{body['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]
    assert fetched.json()["state"] == "draft"


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
