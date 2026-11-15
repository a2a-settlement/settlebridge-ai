"""Cross-owner 403 coverage for /api/bots management routes."""
from __future__ import annotations

import uuid
from unittest.mock import MagicMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User, UserType
from app.routes import bots as bots_routes
from app.services import exchange as exchange_svc


def _user(*, bot_id: str = "acct-owner", key: str = "ate_owner") -> User:
    return User(
        id=uuid.uuid4(),
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        password_hash="x",
        display_name="Operator",
        user_type=UserType.AGENT_OPERATOR,
        exchange_bot_id=bot_id,
        exchange_api_key=key,
        managed_developer_id="dev-mine",
    )


class _Sync:
    def __init__(self, user: User):
        self.user = user

    async def commit(self):
        return None


def _client(user: User) -> TestClient:
    app = FastAPI()
    app.include_router(bots_routes.router, prefix="/api/bots")
    db = _Sync(user)

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def _http_error(code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://exchange.test/v1/accounts/x")
    response = httpx.Response(code, request=request)
    return httpx.HTTPStatusError("err", request=request, response=response)


def test_suspend_maps_exchange_403(monkeypatch):
    user = _user()
    client = _client(user)

    def boom(*_a, **_k):
        raise RuntimeError("403 Forbidden")

    monkeypatch.setattr(exchange_svc, "suspend_managed_account", boom)
    resp = client.post("/api/bots/acct-foreign/suspend")
    assert resp.status_code == 403
    assert "Not authorized" in resp.json()["detail"]


def test_unsuspend_maps_exchange_403(monkeypatch):
    user = _user()
    client = _client(user)

    def boom(*_a, **_k):
        raise RuntimeError("Client error '403'")

    monkeypatch.setattr(exchange_svc, "unsuspend_managed_account", boom)
    resp = client.post("/api/bots/acct-foreign/unsuspend")
    assert resp.status_code == 403


def test_rotate_key_maps_exchange_403(monkeypatch):
    user = _user()
    client = _client(user)

    def boom(*_a, **_k):
        raise RuntimeError("403")

    monkeypatch.setattr(exchange_svc, "rotate_managed_key", boom)
    resp = client.post("/api/bots/acct-foreign/rotate-key")
    assert resp.status_code == 403


def test_patch_settings_rejects_foreign_developer_id(monkeypatch):
    user = _user()
    client = _client(user)
    monkeypatch.setattr(exchange_svc, "own_namespace", lambda _u: "dev-mine")
    resp = client.patch("/api/bots/settings", json={"developer_id": "dev-other"})
    assert resp.status_code == 403
    assert "dev-mine" in resp.json()["detail"]


def test_patch_settings_accepts_own_developer_id(monkeypatch):
    user = _user()
    client = _client(user)
    monkeypatch.setattr(exchange_svc, "own_namespace", lambda _u: "dev-mine")
    resp = client.patch("/api/bots/settings", json={"developer_id": "dev-mine"})
    assert resp.status_code == 200
    assert resp.json()["managed_developer_id"] == "dev-mine"


def test_list_bots_uses_managed_accounts(monkeypatch):
    user = _user(bot_id="acct-owner")
    client = _client(user)

    def list_managed(u):
        assert u.exchange_api_key == "ate_owner"
        return {
            "developer_id": "dev-mine",
            "count": 2,
            "bots": [
                {"id": "acct-owner", "bot_name": "Mine", "status": "active"},
                {"id": "acct-sib", "bot_name": "Sib", "status": "active"},
            ],
        }

    monkeypatch.setattr(exchange_svc, "list_managed_accounts", list_managed)
    resp = client.get("/api/bots")
    assert resp.status_code == 200
    body = resp.json()
    assert body["developer_id"] == "dev-mine"
    own = next(b for b in body["bots"] if b["id"] == "acct-owner")
    assert own["own"] is True
    sib = next(b for b in body["bots"] if b["id"] == "acct-sib")
    assert sib["own"] is False


def test_suspend_own_linked_account_rejected():
    user = _user(bot_id="acct-owner")
    client = _client(user)
    resp = client.post("/api/bots/acct-owner/suspend")
    assert resp.status_code == 400


def test_owner_suspend_happy_path(monkeypatch):
    user = _user()
    client = _client(user)
    monkeypatch.setattr(
        exchange_svc,
        "suspend_managed_account",
        lambda _u, bot_id: {"account_id": bot_id, "status": "suspended"},
    )
    resp = client.post("/api/bots/acct-sib/suspend")
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"
