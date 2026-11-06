"""Sign in with GitHub and the setup page's Add funds. In-memory SQLite; GitHub and exchange faked."""

# ruff: noqa: E402

from __future__ import annotations

import sys
import time
import types
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

_passlib = types.ModuleType("passlib")
_passlib_context = types.ModuleType("passlib.context")


class _CryptContext:
    def __init__(self, *args, **kwargs):
        pass


_passlib_context.CryptContext = _CryptContext
_passlib.context = _passlib_context
sys.modules.setdefault("passlib", _passlib)
sys.modules.setdefault("passlib.context", _passlib_context)

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.github import GitHubIdentity
from app.models.user import User
from app.routes import auth as auth_routes
from app.routes import github_onboarding as onboarding_routes
from app.services import exchange, github_identity, github_login
from tests.test_github_onboarding import Bridge, _run, make_engine, make_user

JWT_SECRET = "state-secret"
CLIENT_ID = "cid-123"
OAUTH_URL = "https://github.example"
API_URL = "https://api.github.example"
CODE = "oauth-code"


@pytest.fixture()
def db():
    eng = make_engine()
    bridge = Bridge(Session(eng, expire_on_commit=False))
    try:
        yield bridge
    finally:
        bridge.sync.close()
        eng.dispose()


@pytest.fixture(autouse=True)
def oauth_env(monkeypatch):
    s = github_identity.settings
    monkeypatch.setattr(s, "GITHUB_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(s, "GITHUB_OAUTH_CLIENT_SECRET", "csecret")
    monkeypatch.setattr(s, "GITHUB_OAUTH_URL", OAUTH_URL)
    monkeypatch.setattr(s, "GITHUB_API_URL", API_URL)
    monkeypatch.setattr(s, "JWT_SECRET", JWT_SECRET)
    monkeypatch.setattr(s, "JWT_ALGORITHM", "HS256")
    monkeypatch.setattr(s, "GITHUB_LOGIN_ENABLED", True)
    monkeypatch.setattr(s, "GITHUB_SELF_SERVE_ONBOARDING", True)
    monkeypatch.setattr(s, "GITHUB_SETUP_FUNDING_ENABLED", True)


@pytest.fixture()
def github(monkeypatch):
    state = {"user": {"id": 42, "login": "octocat"}, "calls": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["calls"] += 1
        if request.url.path == "/login/oauth/access_token":
            assert parse_qs(request.content.decode())["code"] == [CODE]
            return httpx.Response(200, json={"access_token": "ghu_token"})
        if request.url.path == "/user":
            return httpx.Response(200, json=state["user"])
        raise AssertionError(str(request.url))

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(github_identity, "_client", lambda: httpx.AsyncClient(transport=transport))
    return state


@pytest.fixture()
def registry(monkeypatch):
    state = {"calls": [], "fail": False, "deposits": [], "deposit_fail": False}

    def deposit(user, amount, reference):
        if state["deposit_fail"]:
            raise RuntimeError("deposit refused")
        state["deposits"].append((user.exchange_bot_id, amount, reference))
        return {"new_balance": amount}

    monkeypatch.setattr(exchange, "deposit", deposit)

    def register_account(**kwargs):
        state["calls"].append(kwargs)
        if state["fail"]:
            raise RuntimeError("exchange down")
        return {"account": {"id": f"acct-{len(state['calls'])}"}, "api_key": "ate_new_key"}

    monkeypatch.setattr(exchange, "register_account", register_account)
    return state


def _start(next_path=None) -> tuple[str, str]:
    url, nonce = github_login.build_login_url(next_path)
    return parse_qs(urlparse(url).query)["state"][0], nonce


def _users(db: Bridge) -> list[User]:
    db.sync.expire_all()
    return list(db.sync.execute(select(User)).scalars())


# State


def test_login_url_carries_a_nonce_hash_and_safe_next():
    url, nonce = github_login.build_login_url("/dashboard/github/setup?installation_id=1")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == [CLIENT_ID]
    payload = jwt.decode(query["state"][0], JWT_SECRET, algorithms=["HS256"])
    assert payload["purpose"] == "github_login"
    assert nonce not in query["state"][0]
    assert payload["nonce_hash"] == github_login._nonce_hash(nonce)
    assert payload["next"] == "/dashboard/github/setup?installation_id=1"


@pytest.mark.parametrize("bad", ["https://evil.example", "//evil.example", "/\\evil", "x", "/a\nb"])
def test_unsafe_next_is_dropped(bad):
    state, _ = _start(bad)
    assert "next" not in jwt.decode(state, JWT_SECRET, algorithms=["HS256"])


# Sign-in


def test_new_github_user_gets_a_user_identity_and_exchange_account(db, github, registry):
    state, nonce = _start("/dashboard/github/setup")
    result = _run(github_login.complete_login(db, CODE, state, nonce))
    assert result["created"] is True and result["login"] == "octocat"
    assert result["next"] == "/dashboard/github/setup"
    (user,) = _users(db)
    assert user.email == "42@github.settlebridge.ai" and user.display_name == "octocat"
    assert user.exchange_bot_id == "acct-1" and user.exchange_api_key == "ate_new_key"
    identity = db.sync.execute(select(GitHubIdentity)).scalar_one()
    assert identity.user_id == user.id and identity.github_user_id == 42
    assert registry["calls"][0]["contact_email"] == user.email
    assert registry["deposits"] == [("acct-1", 1000, "settlebridge-github-signup")]


def test_no_starter_deposit_when_funding_is_off(db, github, registry, monkeypatch):
    monkeypatch.setattr(github_login.settings, "GITHUB_SETUP_FUNDING_ENABLED", False)
    state, nonce = _start()
    assert _run(github_login.complete_login(db, CODE, state, nonce))["created"] is True
    assert registry["calls"] and registry["deposits"] == []


def test_starter_deposit_failure_still_signs_in(db, github, registry):
    registry["deposit_fail"] = True
    state, nonce = _start()
    result = _run(github_login.complete_login(db, CODE, state, nonce))
    assert result["created"] is True
    assert _users(db)[0].exchange_bot_id == "acct-1"


def test_linked_github_account_signs_in_as_its_user(db, github, registry):
    existing = make_user("solver@example.com")
    db.sync.add(existing)
    db.sync.add(
        GitHubIdentity(
            id=uuid.uuid4(),
            user_id=existing.id,
            github_user_id=42,
            login="old-login",
            verified_at=datetime.now(timezone.utc),
        )
    )
    db.sync.commit()
    state, nonce = _start()
    result = _run(github_login.complete_login(db, CODE, state, nonce))
    assert result["created"] is False and result["user"].id == existing.id
    assert len(_users(db)) == 1 and registry["calls"] == [] and registry["deposits"] == []
    db.sync.expire_all()
    assert db.sync.execute(select(GitHubIdentity)).scalar_one().login == "octocat"


def test_exchange_outage_still_creates_the_user(db, github, registry):
    registry["fail"] = True
    state, nonce = _start()
    result = _run(github_login.complete_login(db, CODE, state, nonce))
    assert result["created"] is True
    assert _users(db)[0].exchange_bot_id is None
    assert registry["deposits"] == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda state, nonce: (state, "wrong-nonce"),
        lambda state, nonce: (state, ""),
        lambda state, nonce: (state + "x", nonce),
        lambda state, nonce: (
            jwt.encode(
                {"purpose": "github_identity", "nonce_hash": github_login._nonce_hash(nonce),
                 "exp": int(time.time()) + 60},
                JWT_SECRET,
                algorithm="HS256",
            ),
            nonce,
        ),
        lambda state, nonce: (
            jwt.encode(
                {"purpose": "github_login", "nonce_hash": github_login._nonce_hash(nonce),
                 "exp": int(time.time()) - 5},
                JWT_SECRET,
                algorithm="HS256",
            ),
            nonce,
        ),
    ],
    ids=["wrong-nonce", "no-nonce", "tampered", "link-state", "expired"],
)
def test_bad_state_or_nonce_is_refused_before_github(db, github, registry, mutate):
    state, nonce = mutate(*_start())
    with pytest.raises(ValueError):
        _run(github_login.complete_login(db, CODE, state, nonce))
    assert github["calls"] == 0 and _users(db) == []


# Routes


def _client(db: Bridge, user=None) -> TestClient:
    app = FastAPI()
    app.include_router(auth_routes.router, prefix="/api/auth")
    app.include_router(onboarding_routes.router, prefix="/api/v1/github")

    async def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


def test_login_routes_are_hidden_when_off(db, monkeypatch):
    monkeypatch.setattr(auth_routes.settings, "GITHUB_LOGIN_ENABLED", False)
    client = _client(db)
    assert client.post("/api/auth/github/start", json={}).status_code == 404
    assert client.post("/api/auth/github/callback", json={}).status_code == 404


def test_login_round_trip_returns_a_session(db, github, registry):
    client = _client(db)
    start = client.post("/api/auth/github/start", json={"next": "/dashboard/github/setup"}).json()
    state = parse_qs(urlparse(start["authorize_url"]).query)["state"][0]
    response = client.post(
        "/api/auth/github/callback", json={"code": CODE, "state": state, "nonce": start["nonce"]}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["access_token"] and body["created"] is True
    assert body["next"] == "/dashboard/github/setup"
    user_id = jwt.decode(body["access_token"], JWT_SECRET, algorithms=["HS256"])["sub"]
    assert user_id == str(_users(db)[0].id)


def test_login_callback_failure_is_400_without_detail(db, github, registry):
    state, _ = _start()
    response = _client(db).post(
        "/api/auth/github/callback", json={"code": CODE, "state": state, "nonce": "nope"}
    )
    assert response.status_code == 400
    assert "state" not in response.json()["detail"]


# Add funds


@pytest.fixture()
def bank(monkeypatch):
    state = {"balance": 100, "deposits": [], "fail": False}

    def dashboard(account_id):
        return {"available": state["balance"], "account_id": account_id}

    def deposit(user, amount, reference):
        if state["fail"]:
            raise RuntimeError("refused")
        state["deposits"].append((user.exchange_bot_id, amount, reference))
        state["balance"] += amount
        return {"new_balance": state["balance"]}

    monkeypatch.setattr(exchange, "get_balance_dashboard", dashboard)
    monkeypatch.setattr(exchange, "deposit", deposit)
    return state


def _signed_in(db: Bridge, *, exchange_account: bool = True) -> User:
    user = make_user(exchange=exchange_account)
    db.sync.add(user)
    db.sync.commit()
    return user


def test_add_funds_deposits_the_fixed_amount(db, bank):
    user = _signed_in(db)
    response = _client(db, user).post("/api/v1/github/setup/add-funds")
    assert response.status_code == 200
    assert response.json() == {"deposited_ate": 1000, "balance_ate": 1100}
    assert bank["deposits"] == [("acct-1", 1000, "settlebridge-github-setup")]


def test_add_funds_creates_a_missing_exchange_account(db, registry, bank):
    user = _signed_in(db, exchange_account=False)
    response = _client(db, user).post("/api/v1/github/setup/add-funds")
    assert response.status_code == 200
    assert registry["calls"] and bank["deposits"][0][0] == "acct-1"
    db.sync.expire_all()
    assert db.sync.get(User, user.id).exchange_bot_id == "acct-1"


def test_add_funds_stops_at_the_cap(db, bank):
    user = _signed_in(db)
    bank["balance"] = 2000
    response = _client(db, user).post("/api/v1/github/setup/add-funds")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "balance_sufficient"
    assert bank["deposits"] == []


def test_add_funds_exchange_refusal_is_502(db, bank):
    user = _signed_in(db)
    bank["fail"] = True
    response = _client(db, user).post("/api/v1/github/setup/add-funds")
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "exchange_unavailable"


def test_add_funds_is_hidden_when_off(db, bank, monkeypatch):
    user = _signed_in(db)
    monkeypatch.setattr(onboarding_routes.settings, "GITHUB_SETUP_FUNDING_ENABLED", False)
    assert _client(db, user).post("/api/v1/github/setup/add-funds").status_code == 404
    info = _client(db, user).get("/api/v1/github/setup/info").json()
    assert info["add_funds"]["enabled"] is False
    assert info["urls"]["install_app"].endswith("/installations/new")

