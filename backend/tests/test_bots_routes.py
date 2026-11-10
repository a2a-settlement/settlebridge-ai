"""Managed bot routes, scoped to the linked exchange account's namespace. Exchange faked."""

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
    posts: list[str] = []

    def get_account(account_id):
        if account_id not in ACCOUNTS:
            raise RuntimeError("404")
        return ACCOUNTS[account_id]

    def own_namespace(u):
        return ACCOUNTS[u.exchange_bot_id]["developer_id"] if u.exchange_bot_id else None

    def list_namespace_bots(developer_id):
        return [
            {"id": bot_id, "bot_name": acct["bot_name"], "status": "active"}
            for bot_id, acct in ACCOUNTS.items()
            if acct["developer_id"] == developer_id
        ]

    def post(url, headers=None, timeout=None):
        posts.append(url)
        return SimpleNamespace(
            status_code=200, raise_for_status=lambda: None, json=lambda: {"ok": True}
        )

    monkeypatch.setattr(routes.exchange_svc, "get_account", get_account)
    monkeypatch.setattr(routes.exchange_svc, "own_namespace", own_namespace)
    monkeypatch.setattr(routes.exchange_svc, "list_namespace_bots", list_namespace_bots)
    monkeypatch.setattr(routes.exchange_svc, "rotate_api_key_with_grace", lambda u: ("new-key", 7))
    monkeypatch.setattr(routes.httpx, "post", post)
    monkeypatch.setattr(routes.settings, "A2A_DASHBOARD_API_KEY", "dash")

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/bots")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    return SimpleNamespace(client=TestClient(app), user=user, db=db, posts=posts)


def test_list_returns_only_the_linked_accounts_namespace(env):
    body = env.client.get("/api/bots").json()
    assert body["developer_id"] == "acme"
    assert {bot["id"]: bot["own"] for bot in body["bots"]} == {OWN: True, PEER: False}


def test_list_ignores_a_requested_developer_id(env):
    body = env.client.get("/api/bots", params={"developer_id": "other"}).json()
    assert body["developer_id"] == "acme"


def test_list_requires_a_linked_account(env):
    env.user.exchange_bot_id = None
    assert env.client.get("/api/bots").status_code == 400


def test_suspend_a_bot_in_the_namespace(env):
    assert env.client.post(f"/api/bots/{PEER}/suspend").status_code == 200
    assert env.posts == [
        f"{routes.settings.effective_exchange_url}/v1/dashboard/agents/{PEER}/suspend"
    ]


@pytest.mark.parametrize("action", ["suspend", "unsuspend"])
def test_suspend_refuses_bots_outside_the_namespace(env, action):
    response = env.client.post(f"/api/bots/{STRANGER}/{action}")
    assert response.status_code == 403
    assert env.posts == []


def test_suspend_unknown_bot_is_404(env):
    assert env.client.post("/api/bots/nope/suspend").status_code == 404
    assert env.posts == []


def test_cannot_suspend_own_linked_account(env):
    assert env.client.post(f"/api/bots/{OWN}/suspend").status_code == 400
    assert env.posts == []


def test_rotate_own_account_stores_the_new_key(env):
    body = env.client.post(f"/api/bots/{OWN}/rotate-key").json()
    assert body["api_key"] == "new-key"
    assert body["bot_name"] == "Own"
    assert body["grace_period_minutes"] == 7
    assert env.user.exchange_api_key == "new-key"
    assert env.db.commits == 1


@pytest.mark.parametrize("bot_id", [PEER, STRANGER])
def test_rotate_refuses_other_bots(env, bot_id):
    assert env.client.post(f"/api/bots/{bot_id}/rotate-key").status_code == 403
    assert env.user.exchange_api_key == "old-key"


def test_settings_accepts_only_the_linked_namespace(env):
    assert env.client.patch("/api/bots/settings", json={"developer_id": "other"}).status_code == 403
    response = env.client.patch("/api/bots/settings", json={"developer_id": "acme"})
    assert response.status_code == 200
    assert env.user.managed_developer_id == "acme"
