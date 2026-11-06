"""Solver setup status. No database and no network."""

# ruff: noqa: E402

from __future__ import annotations

import json
import logging
import sys
import types
import uuid

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

# The route imports get_current_user, which builds a passlib CryptContext at import.
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
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.config import settings
from app.database import get_db
from app.middleware.auth import get_current_user
from app.routes.solver_setup import router
from app.services import exchange, github_leases, solver_setup

TOKEN_URL = "https://gateway.test/oauth/token"
FUNDS_MESSAGE = (
    "Pull requests from your linked GitHub account cannot claim bounties on repos you fund."
)


def _user(**overrides):
    data = {
        "id": uuid.uuid4(),
        "exchange_bot_id": "acct-1",
        "exchange_api_key": "ate_secret",
    }
    data.update(overrides)
    return types.SimpleNamespace(**data)


def _row(
    requester_id,
    required_ema,
    *,
    repo="acme/widget",
    issue=12,
    title="Fix the widget",
    amount=2500,
):
    bounty_id = uuid.uuid4()
    binding = types.SimpleNamespace(bounty_id=bounty_id, repo_full_name=repo, issue_number=issue)
    bounty = types.SimpleNamespace(id=bounty_id, requester_id=requester_id, title=title)
    spec = types.SimpleNamespace(required_ema=required_ema, amount_ate=amount)
    return binding, bounty, spec


def _item(row) -> dict:
    binding, bounty, spec = row
    return {
        "bounty_id": str(binding.bounty_id),
        "repo": binding.repo_full_name,
        "issue_number": binding.issue_number,
        "title": bounty.title,
        "amount_ate": spec.amount_ate,
        "required_ema": float(spec.required_ema),
    }


def _mcp(client_id: str) -> dict:
    return {
        "mcpServers": {
            "a2a-settlement": {
                "command": "a2a-settlement-mcp",
                "env": {
                    "SETTLEBRIDGE_GATEWAY_CLIENT_ID": client_id,
                    "SETTLEBRIDGE_GATEWAY_CLIENT_SECRET": "<your exchange API key>",
                },
            }
        }
    }


def _codes(body) -> list[str]:
    return [step["code"] for step in body["next_steps"]]


class _TokenResponse:
    def __init__(self, status_code: int, body: object = None):
        self.status_code = status_code
        self._body = body

    def json(self):
        if isinstance(self._body, BaseException):
            raise self._body
        return self._body


class _Result:
    def __init__(self, found: bool):
        self._found = found

    def first(self):
        if self._found:
            return (1,)
        return None


class _IdRows:
    def __init__(self, ids):
        self._ids = list(ids)

    def scalars(self):
        return self

    def all(self):
        return list(self._ids)


class _Db:
    def __init__(self, state: dict):
        self._state = state

    async def execute(self, stmt):
        self._state["statements"].append(stmt)
        if "github_bounty_bindings" in str(stmt):
            return _IdRows(self._state.get("hidden_ids", ()))
        return _Result(bool(self._state["funds"]))


def _reputation_message() -> str:
    default = settings.GITHUB_DEFAULT_REQUIRED_EMA
    return f"Most repos require a reputation of {default:g}. Starter repos can be lower."


@pytest.fixture
def world(monkeypatch):
    state = {
        "user": _user(),
        "identity": types.SimpleNamespace(login="octocat"),
        "balance": {"reputation": 1},
        "balance_error": None,
        "rows": [],
        "funds": False,
        "posts": [],
        "post_error": None,
        "post_response": _TokenResponse(500, {}),
        "statements": [],
        "bounty_calls": [],
        "identity_ids": [],
        "balance_users": [],
    }
    state["db"] = _Db(state)

    async def fetch_identity(db, user_id):
        assert user_id == state["user"].id
        state["identity_ids"].append(user_id)
        return state["identity"]

    async def fetch_bounties(db, *, language, limit):
        assert language is None
        assert limit == 100
        state["bounty_calls"].append({"language": language, "limit": limit})
        return list(state["rows"])

    def get_balance(user):
        assert user is state["user"]
        state["balance_users"].append(user)
        if state["balance_error"] is not None:
            raise state["balance_error"]
        return dict(state["balance"])

    def post(url, **kwargs):
        state["posts"].append({"url": url, **kwargs})
        if state["post_error"] is not None:
            raise state["post_error"]
        return state["post_response"]

    monkeypatch.setattr(github_leases, "fetch_identity_for_user", fetch_identity)
    monkeypatch.setattr(github_leases, "fetch_advertised_bounties", fetch_bounties)
    monkeypatch.setattr(exchange, "get_balance", get_balance)
    monkeypatch.setattr(solver_setup.httpx, "post", post)
    monkeypatch.setattr(settings, "TRUST_GATEWAY_TOKEN_URL", "")

    app = FastAPI()
    app.include_router(router)

    async def override_db():
        yield state["db"]

    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[get_current_user] = lambda: state["user"]
    client = TestClient(app)
    state["client"] = client
    try:
        yield state
    finally:
        client.close()


def _get(world):
    response = world["client"].get("/solver/setup-status")
    assert response.status_code == 200, response.text
    assert "ate_secret" not in response.text
    return response, response.json()


def test_route_is_unprefixed_setup_status():
    assert router.prefix == ""
    matches = [route for route in router.routes if isinstance(route, APIRoute)]
    by_path = {route.path: route for route in matches}
    assert set(by_path) == {"/solver/setup-status", "/solver/agent-config"}
    assert by_path["/solver/setup-status"].methods == {"GET"}
    calls = {dep.call for dep in by_path["/solver/setup-status"].dependant.dependencies}
    assert get_current_user in calls
    assert get_db in calls


def test_ready_when_setup_is_complete(world):
    _, body = _get(world)
    assert set(body) == {
        "exchange_account_id",
        "github_login",
        "reputation",
        "financial_scope",
        "eligible_bounties",
        "ineligible_count",
        "next_steps",
        "ready",
        "mcp_config",
        "urls",
    }
    assert body["exchange_account_id"] == "acct-1"
    assert body["github_login"] == "octocat"
    assert body["reputation"] == 1.0
    assert isinstance(body["reputation"], float)
    assert body["financial_scope"] == "unknown"
    assert body["eligible_bounties"] == []
    assert body["ineligible_count"] == 0
    assert body["next_steps"] == []
    assert body["ready"] is True
    assert body["urls"] == {
        "link_github": "/dashboard/settings/github",
        "rotate_key": "/dashboard/settings",
        "bounties": "/bounties",
    }
    assert world["posts"] == []
    assert world["bounty_calls"] == [{"language": None, "limit": 100}]
    assert world["identity_ids"] == [world["user"].id]
    assert world["balance_users"] == [world["user"]]


def test_eligible_ineligible_and_owned_bounties(world):
    user_id = world["user"].id
    other = uuid.uuid4()
    default = settings.GITHUB_DEFAULT_REQUIRED_EMA
    low = _row(
        other, default / 2, repo="acme/widget", issue=12, title="Fix the widget", amount=2500
    )
    exact = _row(other, default, repo="acme/starter", issue=3, title="Starter bug", amount=100)
    high = _row(other, default + 1, repo="acme/secure", issue=9, title="Hard bug", amount=9000)
    owned_easy = _row(user_id, 0.0, repo="acme/mine", issue=1, title="My bounty", amount=50)
    owned_hard = _row(user_id, default + 1, repo="acme/mine", issue=2, title="My other", amount=80)
    world["rows"] = [low, owned_easy, high, exact, owned_hard]
    world["balance"] = {"reputation": str(default)}
    _, body = _get(world)
    assert body["reputation"] == default
    assert isinstance(body["reputation"], float)
    assert body["eligible_bounties"] == [_item(low), _item(exact)]
    assert body["ineligible_count"] == 1


def test_github_not_linked_only_when_missing(world):
    world["identity"] = None
    _, body = _get(world)
    assert _codes(body) == ["github_not_linked"]
    step = body["next_steps"][0]
    assert step["blocking"] is True
    assert step["url"] == "/dashboard/settings/github"
    assert step["message"] == "Link the GitHub account that will open your pull requests."
    assert body["github_login"] is None
    assert body["exchange_account_id"] == "acct-1"
    assert body["ready"] is False


def test_low_reputation_is_not_blocking(world):
    world["balance"] = {"reputation": 0}
    _, body = _get(world)
    assert _codes(body) == ["reputation_below_most_repos"]
    step = body["next_steps"][0]
    assert step["blocking"] is False
    assert step["url"] is None
    assert step["message"] == _reputation_message()
    assert body["reputation"] == 0.0
    assert isinstance(body["reputation"], float)
    assert body["ready"] is True


def test_reputation_at_default_has_no_step(world):
    world["balance"] = {"reputation": settings.GITHUB_DEFAULT_REQUIRED_EMA}
    _, body = _get(world)
    assert body["reputation"] == settings.GITHUB_DEFAULT_REQUIRED_EMA
    assert _codes(body) == []
    assert body["ready"] is True


def test_funding_repos_is_not_blocking(world):
    world["funds"] = True
    _, body = _get(world)
    assert _codes(body) == ["funds_own_repos"]
    step = body["next_steps"][0]
    assert step["blocking"] is False
    assert step["url"] is None
    assert step["message"] == FUNDS_MESSAGE
    assert body["ready"] is True
    stmt = world["statements"][-1]
    assert "funder_user_id" in str(stmt)
    assert world["user"].id in stmt.compile().params.values()


def test_nonblocking_steps_together_stay_ready(world):
    world["balance"] = {"reputation": 0}
    world["funds"] = True
    _, body = _get(world)
    assert _codes(body) == ["reputation_below_most_repos", "funds_own_repos"]
    assert body["ready"] is True
    assert all(step["blocking"] is False for step in body["next_steps"])


def test_missing_account_orders_steps_and_skips_probe(world, monkeypatch):
    user = _user(exchange_bot_id=None, exchange_api_key="ate_secret")
    world["user"] = user
    world["identity"] = None
    world["funds"] = True
    world["rows"] = [
        _row(uuid.uuid4(), 0.1, title="Easy", repo="acme/easy", issue=4),
        _row(user.id, 0.0, title="Mine", repo="acme/mine", issue=5),
    ]
    monkeypatch.setattr(settings, "TRUST_GATEWAY_TOKEN_URL", TOKEN_URL)
    _, body = _get(world)
    assert _codes(body) == ["no_exchange_account", "github_not_linked", "funds_own_repos"]
    assert body["next_steps"][0]["blocking"] is True
    assert body["next_steps"][0]["url"] == "/dashboard/settings"
    assert body["next_steps"][0]["message"] == (
        "Create an exchange account and sign in to SettleBridge with its API key."
    )
    assert body["exchange_account_id"] is None
    assert body["reputation"] is None
    assert body["financial_scope"] == "unknown"
    assert body["eligible_bounties"] == []
    assert body["ineligible_count"] == 1
    assert body["ready"] is False
    assert world["posts"] == []
    assert world["balance_users"] == []
    assert body["mcp_config"] == _mcp("<account id>")


def test_steps_are_ordered(world, monkeypatch):
    world["identity"] = None
    world["balance"] = {"reputation": 0}
    world["funds"] = True
    monkeypatch.setattr(settings, "TRUST_GATEWAY_TOKEN_URL", TOKEN_URL)
    world["post_response"] = _TokenResponse(400, {"access_token": "tok_hidden"})
    response, body = _get(world)
    assert _codes(body) == [
        "github_not_linked",
        "reputation_below_most_repos",
        "funds_own_repos",
    ]
    assert [step["blocking"] for step in body["next_steps"]] == [True, False, False]
    assert [step["url"] for step in body["next_steps"]] == [
        "/dashboard/settings/github",
        None,
        None,
    ]
    assert body["financial_scope"] == "denied"
    assert body["ready"] is False
    assert "financial_scope_denied" not in response.text
    assert "tok_hidden" not in response.text


def test_mcp_config_never_contains_api_key(world):
    response, body = _get(world)
    assert body["mcp_config"] == _mcp("acct-1")
    assert "ate_secret" not in response.text

    world["user"] = _user(exchange_bot_id=None, exchange_api_key="ate_secret")
    response, body = _get(world)
    assert body["mcp_config"] == _mcp("<account id>")
    assert "ate_secret" not in response.text
    env = body["mcp_config"]["mcpServers"]["a2a-settlement"]["env"]
    assert env["SETTLEBRIDGE_GATEWAY_CLIENT_SECRET"] == "<your exchange API key>"


def test_probe_granted(world, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.services.solver_setup")
    monkeypatch.setattr(settings, "TRUST_GATEWAY_TOKEN_URL", TOKEN_URL)
    world["post_response"] = _TokenResponse(200, {"access_token": "tok_hidden"})
    response, body = _get(world)
    assert body["financial_scope"] == "granted"
    assert body["next_steps"] == []
    assert body["ready"] is True
    assert "tok_hidden" not in response.text
    assert "tok_hidden" not in caplog.text
    assert "ate_secret" not in caplog.text
    assert world["posts"] == [
        {
            "url": TOKEN_URL,
            "data": {
                "grant_type": "client_credentials",
                "client_id": "acct-1",
                "client_secret": "ate_secret",
                "scope": "mcp:read mcp:tool:financial",
            },
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
            "timeout": 5.0,
        }
    ]


@pytest.mark.parametrize("status_code", [400, 403])
def test_probe_denied(world, monkeypatch, caplog, status_code):
    caplog.set_level(logging.INFO, logger="app.services.solver_setup")
    monkeypatch.setattr(settings, "TRUST_GATEWAY_TOKEN_URL", TOKEN_URL)
    world["post_response"] = _TokenResponse(
        status_code, {"error": "invalid_client", "access_token": "tok_hidden"}
    )
    response, body = _get(world)
    assert body["financial_scope"] == "denied"
    assert _codes(body) == []
    assert body["ready"] is True
    assert "financial_scope_denied" not in response.text
    assert "refused the mcp:read" not in response.text
    assert "tok_hidden" not in response.text
    assert "tok_hidden" not in caplog.text
    assert "ate_secret" not in caplog.text
    assert str(status_code) in caplog.text


def test_probe_skipped_when_url_empty_or_key_missing(world, monkeypatch):
    world["post_response"] = _TokenResponse(200, {"access_token": "tok_hidden"})
    response, body = _get(world)
    assert body["financial_scope"] == "unknown"
    assert world["posts"] == []
    assert "tok_hidden" not in response.text

    monkeypatch.setattr(settings, "TRUST_GATEWAY_TOKEN_URL", TOKEN_URL)
    world["user"] = _user(exchange_api_key=None)
    _, body = _get(world)
    assert body["exchange_account_id"] == "acct-1"
    assert body["financial_scope"] == "unknown"
    assert world["posts"] == []
    assert "financial_scope_denied" not in _codes(body)


def test_probe_other_results_are_unknown(world, monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger="app.services.solver_setup")
    monkeypatch.setattr(settings, "TRUST_GATEWAY_TOKEN_URL", TOKEN_URL)
    hidden = {"access_token": "tok_hidden", "echo": "ate_secret"}
    for response_obj in (
        _TokenResponse(500, hidden),
        _TokenResponse(401, hidden),
        _TokenResponse(200, {"access_token": ""}),
        _TokenResponse(200, {}),
        _TokenResponse(200, {"access_token": None}),
    ):
        caplog.clear()
        world["posts"].clear()
        world["post_error"] = None
        world["post_response"] = response_obj
        response, body = _get(world)
        assert body["financial_scope"] == "unknown"
        assert "tok_hidden" not in response.text
        assert "ate_secret" not in caplog.text
        assert len(world["posts"]) == 1

    caplog.clear()
    world["post_error"] = None
    world["post_response"] = _TokenResponse(200, json.JSONDecodeError("Expecting value", "", 0))
    response, body = _get(world)
    assert body["financial_scope"] == "unknown"
    assert "JSONDecodeError" in caplog.text
    assert "Expecting value" not in caplog.text

    caplog.clear()
    world["post_error"] = RuntimeError("ate_secret")
    response, body = _get(world)
    assert body["financial_scope"] == "unknown"
    assert "RuntimeError" in caplog.text
    assert "ate_secret" not in caplog.text
    assert "ate_secret" not in response.text


def test_balance_failure_returns_200(world, caplog):
    caplog.set_level(logging.INFO, logger="app.services.solver_setup")
    other = uuid.uuid4()
    world["rows"] = [
        _row(other, 0.1, title="Easy", repo="acme/easy", issue=1),
        _row(world["user"].id, 0.1, title="Mine", repo="acme/mine", issue=2),
        _row(other, 0.95, title="Hard", repo="acme/hard", issue=3),
    ]
    world["balance_error"] = RuntimeError("ate_secret")
    response, body = _get(world)
    assert response.status_code == 200
    assert body["reputation"] is None
    assert body["eligible_bounties"] == []
    assert body["ineligible_count"] == 2
    assert "no_exchange_account" not in _codes(body)
    assert "reputation_below_most_repos" not in _codes(body)
    assert "RuntimeError" in caplog.text
    assert "ate_secret" not in caplog.text


def test_hidden_bounties_are_excluded_from_eligible_and_ineligible(world):
    user_id = world["user"].id
    other = uuid.uuid4()
    default = settings.GITHUB_DEFAULT_REQUIRED_EMA
    visible_ok = _row(other, default / 2, repo="acme/pub", issue=1, title="Visible bug")
    visible_hard = _row(other, default + 5, repo="acme/hard", issue=2, title="Hard bug")
    hidden_ok = _row(other, 0, repo="acme/secret", issue=3, title="Secret eligible")
    hidden_hard = _row(other, default + 9, repo="acme/secret", issue=4, title="Secret hard")
    hidden_owned = _row(user_id, 0, repo="acme/mine", issue=5, title="Secret mine")
    world["rows"] = [visible_ok, visible_hard, hidden_ok, hidden_hard, hidden_owned]
    world["balance"] = {"reputation": default}
    world["hidden_ids"] = {hidden_ok[1].id, hidden_hard[1].id, hidden_owned[1].id}
    response, body = _get(world)
    assert body["eligible_bounties"] == [_item(visible_ok)]
    assert body["ineligible_count"] == 1
    assert "Secret" not in response.text
    assert any("github_bounty_bindings" in str(stmt) for stmt in world["statements"])
