"""Managed agent routes, ownership-scoped via exchange helpers. Exchange faked."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.middleware.auth import get_current_user
from app.routes import bots as routes

OWN = "own-bot"
PEER = "peer-bot"
STRANGER = "stranger-bot"
ACCOUNTS = {
    OWN: {"bot_name": "Own", "developer_id": "acme"},
    PEER: {"bot_name": "Peer", "developer_id": "acme"},
    STRANGER: {"bot_name": "Stranger", "developer_id": "other"},
}


class _Db:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


@pytest.fixture()
def env(monkeypatch):
    user = SimpleNamespace(
        id=uuid.uuid4(),
        exchange_bot_id=OWN,
        exchange_api_key="old-key",
        managed_developer_id=None,
    )
    db = _Db()
    calls: list[tuple] = []

    def get_account(account_id):
        if account_id not in ACCOUNTS:
            raise RuntimeError("404")
        return ACCOUNTS[account_id]

    def own_namespace(u):
        return ACCOUNTS[u.exchange_bot_id]["developer_id"] if u.exchange_bot_id else None

    def list_managed_accounts(u):
        calls.append(("list", u.exchange_bot_id))
        bots = [
            {"id": bot_id, "bot_name": acct["bot_name"], "status": "active"}
            for bot_id, acct in ACCOUNTS.items()
            if acct["developer_id"] == "acme"
        ]
        return {"developer_id": "acme", "bots": bots, "count": len(bots)}

    def rotate_managed_key(u, bot_id):
        calls.append(("rotate", bot_id))
        if ACCOUNTS.get(bot_id, {}).get("developer_id") != "acme":
            raise RuntimeError("403")
        return {"api_key": "new-key", "grace_period_minutes": 7}

    def suspend_managed_account(u, bot_id):
        calls.append(("suspend", bot_id))
        if bot_id not in ACCOUNTS:
            raise RuntimeError("404")
        if ACCOUNTS[bot_id]["developer_id"] != "acme":
            raise RuntimeError("403")
        return {"account_id": bot_id, "status": "suspended"}

    def unsuspend_managed_account(u, bot_id):
        calls.append(("unsuspend", bot_id))
        if bot_id not in ACCOUNTS:
            raise RuntimeError("404")
        if ACCOUNTS[bot_id]["developer_id"] != "acme":
            raise RuntimeError("403")
        return {"account_id": bot_id, "status": "active"}

    monkeypatch.setattr(routes.exchange_svc, "get_account", get_account)
    monkeypatch.setattr(routes.exchange_svc, "own_namespace", own_namespace)
    monkeypatch.setattr(routes.exchange_svc, "list_managed_accounts", list_managed_accounts)
    monkeypatch.setattr(routes.exchange_svc, "rotate_managed_key", rotate_managed_key)
    monkeypatch.setattr(routes.exchange_svc, "suspend_managed_account", suspend_managed_account)
    monkeypatch.setattr(routes.exchange_svc, "unsuspend_managed_account", unsuspend_managed_account)

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/bots")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return SimpleNamespace(client=TestClient(app), user=user, db=db, calls=calls)


def test_list_returns_only_the_linked_accounts_namespace(env):
    body = env.client.get("/api/bots").json()
    assert body["developer_id"] == "acme"
    assert {bot["id"]: bot["own"] for bot in body["bots"]} == {OWN: True, PEER: False}


def test_list_requires_a_linked_account(env):
    env.user.exchange_bot_id = None
    assert env.client.get("/api/bots").status_code == 400


def test_owner_suspends_peer(env):
    assert env.client.post(f"/api/bots/{PEER}/suspend").status_code == 200
    assert ("suspend", PEER) in env.calls


def test_owner_cannot_suspend_own_linked_account(env):
    assert env.client.post(f"/api/bots/{OWN}/suspend").status_code == 400
    assert env.calls == []


def test_cross_owner_suspend_is_403(env):
    response = env.client.post(f"/api/bots/{STRANGER}/suspend")
    assert response.status_code == 403
    assert ("suspend", STRANGER) in env.calls


def test_cross_owner_unsuspend_is_403(env):
    assert env.client.post(f"/api/bots/{STRANGER}/unsuspend").status_code == 403


def test_unknown_bot_suspend_is_404(env):
    assert env.client.post("/api/bots/nope/suspend").status_code == 404


def test_rotate_own_account_stores_the_new_key(env):
    body = env.client.post(f"/api/bots/{OWN}/rotate-key").json()
    assert body["api_key"] == "new-key"
    assert body["bot_name"] == "Own"
    assert body["grace_period_minutes"] == 7
    assert env.user.exchange_api_key == "new-key"
    assert env.db.commits == 1


def test_rotate_peer_in_namespace(env):
    assert env.client.post(f"/api/bots/{PEER}/rotate-key").status_code == 200
    assert env.user.exchange_api_key == "old-key"


def test_rotate_refuses_stranger(env):
    assert env.client.post(f"/api/bots/{STRANGER}/rotate-key").status_code == 403
    assert env.user.exchange_api_key == "old-key"


def test_settings_accepts_only_the_linked_namespace(env):
    assert env.client.patch("/api/bots/settings", json={"developer_id": "other"}).status_code == 403
    response = env.client.patch("/api/bots/settings", json={"developer_id": "acme"})
    assert response.status_code == 200
    assert env.user.managed_developer_id == "acme"
