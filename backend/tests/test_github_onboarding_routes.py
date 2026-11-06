"""Self-serve onboarding routes. In-memory SQLite; GitHub and exchange faked."""

# ruff: noqa: E402

from __future__ import annotations

import sys
import types
import uuid
from datetime import datetime, timezone

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

# The route imports get_current_user, whose module builds a passlib CryptContext at import.
_passlib = types.ModuleType("passlib")
_passlib_context = types.ModuleType("passlib.context")


class _CryptContext:
    def __init__(self, *args, **kwargs):
        pass


_passlib_context.CryptContext = _CryptContext
_passlib.context = _passlib_context
sys.modules.setdefault("passlib", _passlib)
sys.modules.setdefault("passlib.context", _passlib_context)

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.github import GitHubIdentity
from app.routes import github_onboarding as routes
from tests.test_github_onboarding import (
    ACTIONS,
    REPO,
    Bridge,
    FakeGitHub,
    make_engine,
    make_user,
    reload,
    seed,
)

URL = f"/api/v1/github/repos/{REPO}/onboard"
BODY = {"checks": [{"name": "test", "app_id": ACTIONS}]}


@pytest.fixture()
def db():
    eng = make_engine()
    bridge = Bridge(Session(eng, expire_on_commit=False))
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


@pytest.fixture()
def gh(monkeypatch):
    return FakeGitHub().install(monkeypatch)


@pytest.fixture(autouse=True)
def enabled(monkeypatch):
    monkeypatch.setattr(routes.settings, "GITHUB_SELF_SERVE_ONBOARDING", True)


@pytest.fixture(autouse=True)
def balance(monkeypatch):
    from app.services import github_onboarding

    async def open_cost(db, user_id):
        return 0

    monkeypatch.setattr(
        github_onboarding.exchange,
        "get_balance_dashboard",
        lambda account_id: {"available": 5_000, "account_id": account_id},
    )
    monkeypatch.setattr(github_onboarding, "sum_advertised_cost", open_cost)


def _client(db: Bridge, user) -> TestClient:
    app = FastAPI()
    app.include_router(routes.router, prefix="/api/v1/github")

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _setup(db: Bridge, *, identity: bool = True, **link_values):
    user = make_user()
    db.sync.add(user)
    if identity:
        db.sync.add(
            GitHubIdentity(
                id=uuid.uuid4(),
                user_id=user.id,
                github_user_id=4242,
                login="pat",
                verified_at=datetime.now(timezone.utc),
            )
        )
    seed(db.sync, **link_values)
    db.sync.commit()
    return user


def test_routes_are_hidden_when_self_serve_is_off(db, gh, monkeypatch):
    user = _setup(db)
    monkeypatch.setattr(routes.settings, "GITHUB_SELF_SERVE_ONBOARDING", False)
    client = _client(db, user)
    assert client.get(URL).status_code == 404
    assert client.post(URL, json=BODY).status_code == 404


def test_requires_a_linked_github_identity(db, gh):
    user = _setup(db, identity=False)
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 403
    assert "link your GitHub account" in response.json()["detail"]


@pytest.mark.parametrize(
    "record", [("write", 4242), ("maintain", 4242), ("none", None), ("admin", 999)]
)
def test_requires_admin_on_the_same_github_user(db, gh, record):
    user = _setup(db)
    gh.record = record
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 403
    assert reload(db).enabled is False


def test_unknown_repo_is_404(db, gh):
    user = _setup(db)
    response = _client(db, user).post("/api/v1/github/repos/acme/other/onboard", json=BODY)
    assert response.status_code == 404


def test_invalid_repo_name_is_422(db, gh):
    user = _setup(db)
    response = _client(db, user).get("/api/v1/github/repos/acme/bad%20name/onboard")
    assert response.status_code == 422


def test_github_failure_is_502(db, gh):
    user = _setup(db)
    gh.fail = "get_collaborator_record"
    assert _client(db, user).post(URL, json=BODY).status_code == 502


def test_preview_returns_detection_without_writing(db, gh):
    user = _setup(db)
    response = _client(db, user).get(URL)
    assert response.status_code == 200
    payload = response.json()
    assert payload["detected"]["required_checks"] == [{"name": "test", "app_id": ACTIONS}]
    assert payload["enabled"] is False
    assert payload["funded_by_you"] is False and payload["funded_by_other"] is False
    assert db.commits == 0


def test_post_requires_explicit_checks(db, gh):
    user = _setup(db)
    client = _client(db, user)
    assert client.post(URL, json={}).status_code == 422
    assert client.post(URL, json={"checks": []}).status_code == 422
    assert client.post(URL, json={**BODY, "funder_email": "x@example.com"}).status_code == 422


def test_admin_onboards_as_the_funder(db, gh):
    user = _setup(db)
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 200
    payload = response.json()
    assert payload["changed"] is True and payload["dry_run"] is False
    link = reload(db)
    assert link.enabled is True
    assert link.funder_user_id == user.id
    assert link.onboarded_by_user_id == user.id


def test_dry_run_rolls_back(db, gh):
    user = _setup(db)
    response = _client(db, user).post(URL, json={**BODY, "dry_run": True})
    assert response.status_code == 200
    assert response.json()["changed"] is True
    assert reload(db).enabled is False


def test_different_funder_is_409(db, gh):
    user = _setup(db)
    other = make_user("owner@example.com")
    db.sync.add(other)
    db.sync.flush()
    link = reload(db)
    link.funder_user_id = other.id
    db.sync.commit()
    response = _client(db, user).post(URL, json=BODY)
    assert response.status_code == 409
    assert reload(db).funder_user_id == other.id


def test_service_validation_is_422(db, gh):
    user = _setup(db)
    response = _client(db, user).post(URL, json={**BODY, "allowed_paths": ["*"]})
    assert response.status_code == 422
    assert ".github" in response.json()["detail"]
    assert reload(db).enabled is False
