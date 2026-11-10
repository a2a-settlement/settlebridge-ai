"""Platform admin allowlist and the gateway route gate. No database and no network."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user, is_platform_admin
from app.models.user import UserType
from app.routes import gateway as gateway_routes
from app.schemas.user import UserResponse

RULE_ID = "22222222-2222-2222-2222-222222222222"
POLICY_ID = "11111111-1111-1111-1111-111111111111"
REGISTRATION_DENIED = "Utility agent registration is disabled or the registration secret is invalid"
REGISTER_BODY = {
    "bot_name": "utility",
    "developer_id": "dev",
    "developer_name": "Dev",
    "contact_email": "dev@example.com",
}


class _Rows:
    def all(self):
        return []


class _Result:
    def scalars(self):
        return _Rows()


class _Session:
    def __init__(self):
        self.entered = False

    async def execute(self, *_args, **_kwargs):
        return _Result()


def _client(monkeypatch, user_id: uuid.UUID, allowlist: str):
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", allowlist)
    session = _Session()
    user = SimpleNamespace(id=user_id)
    app = FastAPI()
    app.include_router(gateway_routes.registration_router, prefix="/api/gateway")
    app.include_router(gateway_routes.router, prefix="/api/gateway")

    async def override_db():
        session.entered = True
        yield session

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return SimpleNamespace(client=TestClient(app), session=session, user=user)


@pytest.fixture()
def non_admin(monkeypatch):
    return _client(monkeypatch, uuid.uuid4(), str(uuid.uuid4()))


@pytest.fixture()
def admin(monkeypatch):
    user_id = uuid.uuid4()
    return _client(monkeypatch, user_id, str(user_id))


def test_empty_allowlist_means_nobody_is_admin(monkeypatch):
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", "")
    assert is_platform_admin(uuid.uuid4()) is False


def test_listed_id_is_platform_admin(monkeypatch):
    admin_id = uuid.uuid4()
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", str(admin_id))
    assert is_platform_admin(admin_id) is True
    assert is_platform_admin(SimpleNamespace(id=admin_id)) is True
    assert is_platform_admin(uuid.uuid4()) is False


def test_invalid_allowlist_entries_are_ignored(monkeypatch):
    admin_id = uuid.uuid4()
    monkeypatch.setattr(
        settings,
        "PLATFORM_ADMIN_USER_IDS",
        f"not-a-uuid,{admin_id},still-bad",
    )
    assert is_platform_admin(admin_id) is True
    assert is_platform_admin(uuid.uuid4()) is False


def test_allowlist_whitespace_is_tolerated(monkeypatch):
    admin_id = uuid.uuid4()
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", f"  {admin_id}  ,   ")
    assert is_platform_admin(admin_id) is True


def test_user_response_includes_is_admin(monkeypatch):
    user_id = uuid.uuid4()
    user = SimpleNamespace(
        id=user_id,
        email="ada@example.com",
        display_name="Ada",
        user_type=UserType.BOTH,
        exchange_bot_id=None,
        exchange_balance_cached=None,
        managed_developer_id=None,
        account_kind=None,
        bio=None,
        created_at=datetime.now(timezone.utc),
    )
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", "")
    assert UserResponse.model_validate(user).model_dump()["is_admin"] is False
    monkeypatch.setattr(settings, "PLATFORM_ADMIN_USER_IDS", str(user_id))
    assert UserResponse.model_validate(user).model_dump()["is_admin"] is True


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/gateway/policies", None),
        ("POST", "/api/gateway/policies", {"name": "baseline", "yaml_content": "rules: []"}),
        ("PUT", f"/api/gateway/alerts/rules/{RULE_ID}", {"name": "renamed"}),
        ("DELETE", f"/api/gateway/policies/{POLICY_ID}", None),
    ],
)
def test_non_admin_is_forbidden_before_the_database(non_admin, method, path, body):
    kwargs = {} if body is None else {"json": body}
    response = non_admin.client.request(method, path, **kwargs)
    assert response.status_code == 403
    assert response.json()["detail"] == "Platform admin only"
    assert non_admin.session.entered is False


def test_admin_lists_policies(admin):
    response = admin.client.get("/api/gateway/policies")
    assert response.status_code == 200
    assert response.json() == []
    assert admin.session.entered is True


def test_register_missing_secret_is_not_admin_gated(non_admin):
    response = non_admin.client.post("/api/gateway/agents/register", json=REGISTER_BODY)
    assert response.status_code == 403
    assert response.json()["detail"] == REGISTRATION_DENIED


def test_register_wrong_secret_is_not_admin_gated(non_admin, monkeypatch):
    monkeypatch.setattr(settings, "UTILITY_AGENT_REGISTRATION_SECRET", "expected")
    response = non_admin.client.post(
        "/api/gateway/agents/register",
        json=REGISTER_BODY,
        headers={"X-SettleBridge-Registration-Secret": "wrong"},
    )
    assert response.status_code == 403
    assert response.json()["detail"] == REGISTRATION_DENIED
