"""One-time solver agent config. No database and no network."""

# ruff: noqa: E402

from __future__ import annotations

import logging
import sys
import types

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

from app.config import Settings, settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.routes import solver_setup as solver_setup_routes

URL = "/api/v1/github/solver/agent-config"
OLD = "ate_old_key"
NEW = "ate_new_key"
ACCOUNT = "acct-1"
HOSTED = "https://mcp.test/mcp"
ROTATED = "2026-11-06T23:15:00Z"
EXCHANGE = "https://exchange.test"


class _Db:
    def __init__(self, user):
        self.user = user
        self.commits = 0
        self.key_at_commit = None

    async def commit(self):
        self.commits += 1
        self.key_at_commit = self.user.exchange_api_key


def _expected() -> dict:
    return {
        "account_id": ACCOUNT,
        "api_key": NEW,
        "rotated_at": ROTATED,
        "hosted": {
            "mcpServers": {
                "settlebridge": {
                    "url": HOSTED,
                    "headers": {"Authorization": f"Bearer {NEW}"},
                }
            }
        },
        "local": {
            "mcpServers": {
                "a2a-settlement": {
                    "command": "a2a-settlement-mcp",
                    "env": {
                        "SETTLEBRIDGE_GATEWAY_CLIENT_ID": ACCOUNT,
                        "SETTLEBRIDGE_GATEWAY_CLIENT_SECRET": NEW,
                    },
                }
            }
        },
    }


@pytest.fixture
def world(monkeypatch):
    user = types.SimpleNamespace(exchange_bot_id=ACCOUNT, exchange_api_key=OLD)
    db = _Db(user)
    state = {"calls": [], "mode": "ok", "user": user, "db": db}

    def post(url, headers=None, timeout=None, **kwargs):
        state["calls"].append(
            {"url": url, "headers": headers, "timeout": timeout, "extra": kwargs}
        )
        mode = state["mode"]
        if mode == "raise":
            raise RuntimeError(f"down {OLD} {NEW}")
        if mode == "status":
            return httpx.Response(
                503,
                json={"api_key": NEW, "detail": OLD},
                request=httpx.Request("POST", url),
            )
        if mode == "missing":
            return httpx.Response(
                200,
                json={"grace_period_minutes": 5, "detail": OLD},
                request=httpx.Request("POST", url),
            )
        return httpx.Response(
            200,
            json={"api_key": NEW, "grace_period_minutes": 5},
            request=httpx.Request("POST", url),
        )

    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(settings, "GATEWAY_EXCHANGE_URL", "")
    monkeypatch.setattr(settings, "A2A_EXCHANGE_URL", EXCHANGE)
    monkeypatch.setattr(settings, "SETTLEBRIDGE_HOSTED_MCP_URL", HOSTED)
    monkeypatch.setattr(solver_setup_routes, "_rotated_at", lambda: ROTATED)

    app = FastAPI()
    app.include_router(solver_setup_routes.router, prefix="/api/v1/github")

    async def override_db():
        yield db

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: user
    client = TestClient(app)
    state["app"] = app
    state["client"] = client
    try:
        yield state
    finally:
        client.close()


def test_hosted_mcp_url_default():
    assert (
        Settings.model_fields["SETTLEBRIDGE_HOSTED_MCP_URL"].default
        == "https://mcp.a2a-settlement.org/mcp"
    )


def test_agent_config_rotates_and_returns_both_shapes(world, caplog):
    caplog.set_level(logging.DEBUG)
    response = world["client"].post(URL)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == _expected()
    assert world["user"].exchange_api_key == NEW
    assert world["db"].commits == 1
    assert world["db"].key_at_commit == NEW
    assert world["calls"] == [
        {
            "url": f"{EXCHANGE}/v1/accounts/rotate-key",
            "headers": {"Authorization": f"Bearer {OLD}"},
            "timeout": 10,
            "extra": {},
        }
    ]
    assert OLD not in response.text
    assert OLD not in caplog.text
    assert NEW not in caplog.text


@pytest.mark.parametrize("mode", ["raise", "status", "missing"])
def test_rotate_failure_is_502_and_keeps_the_stored_key(world, caplog, mode):
    caplog.set_level(logging.DEBUG)
    world["mode"] = mode
    response = world["client"].post(URL)
    assert response.status_code == 502
    assert response.json() == {"error": "exchange_unavailable"}
    assert world["user"].exchange_api_key == OLD
    assert world["db"].commits == 0
    assert world["db"].key_at_commit is None
    assert len(world["calls"]) == 1
    assert OLD not in response.text
    assert NEW not in response.text
    assert OLD not in caplog.text
    assert NEW not in caplog.text


@pytest.mark.parametrize(
    ("bot_id", "api_key"),
    [
        (None, OLD),
        (ACCOUNT, None),
        (None, None),
        ("", OLD),
        (ACCOUNT, ""),
    ],
)
def test_missing_exchange_account_is_409(world, bot_id, api_key):
    world["user"].exchange_bot_id = bot_id
    world["user"].exchange_api_key = api_key
    response = world["client"].post(URL)
    assert response.status_code == 409
    assert response.json() == {"error": "no_exchange_account"}
    assert world["calls"] == []
    assert world["db"].commits == 0
    assert world["user"].exchange_api_key == api_key


def test_agent_config_requires_a_signed_in_user(world, caplog):
    caplog.set_level(logging.DEBUG)
    world["app"].dependency_overrides.pop(get_current_user)
    response = world["client"].post(URL)
    assert response.status_code == 401
    assert world["calls"] == []
    assert world["db"].commits == 0
    assert world["user"].exchange_api_key == OLD
    assert OLD not in caplog.text
    assert NEW not in caplog.text
