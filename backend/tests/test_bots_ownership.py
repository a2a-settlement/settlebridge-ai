"""Cross-owner 403 and BFF contract coverage for /api/bots and /api/agents."""
from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.user import User, UserType
from app.routes import agents as agents_routes
from app.routes import bots as bots_routes
from app.services import exchange as exchange_svc


def _user(*, bot_id: str = "acct-owner", key: str = "ate_owner") -> User:
    return User(
        id=uuid.uuid4(),
        email="operator@example.com",
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


def _http_error(code: int, body: dict | str | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://exchange.test/v1/accounts/x")
    if isinstance(body, dict):
        response = httpx.Response(code, json=body, request=request)
    elif isinstance(body, str):
        response = httpx.Response(code, text=body, request=request)
    else:
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


def test_list_bots_uses_managed_accounts_and_defaults_new_fields(monkeypatch):
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
    assert body["developer_ids"] == ["dev-mine"]
    assert body["caller_account_type"] == "agent"
    assert body["gateway_claimed_agents"] == []
    own = next(b for b in body["bots"] if b["id"] == "acct-owner")
    assert own["own"] is True
    sib = next(b for b in body["bots"] if b["id"] == "acct-sib")
    assert sib["own"] is False


def test_list_bots_passes_through_multi_id_and_gateway_fields(monkeypatch):
    user = _user(bot_id="acct-gw")
    client = _client(user)

    monkeypatch.setattr(
        exchange_svc,
        "list_managed_accounts",
        lambda _u: {
            "developer_id": "dev-primary",
            "developer_ids": ["dev-primary", "dev-second"],
            "caller_account_type": "gateway",
            "bots": [
                {
                    "id": "acct-gw",
                    "bot_name": "GatewayOne",
                    "developer_id": "dev-primary",
                    "status": "active",
                    "has_agent_card": True,
                    "last_activity_at": "2026-11-15T12:00:00Z",
                    "gateway_claims": [],
                }
            ],
            "gateway_claimed_agents": [{"id": "acct-ext", "bot_name": "ExtBot"}],
            "count": 1,
        },
    )
    resp = client.get("/api/bots")
    assert resp.status_code == 200
    body = resp.json()
    assert body["developer_ids"] == ["dev-primary", "dev-second"]
    assert body["caller_account_type"] == "gateway"
    assert body["gateway_claimed_agents"] == [{"id": "acct-ext", "bot_name": "ExtBot"}]
    assert body["bots"][0]["has_agent_card"] is True
    assert body["bots"][0]["own"] is True


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


# ---------------------------------------------------------------------------
# Phase 2: Multi-ID attach/detach, register, card publish, settlements
# ---------------------------------------------------------------------------


def test_attach_developer_id_happy_path_and_never_logs_key(monkeypatch, caplog):
    user = _user(key="ate_caller_secret")
    client = _client(user)
    secret_agent_key = "ate_target_super_secret_999"
    captured: dict = {}

    def fake_post(url, *, json=None, headers=None, timeout=10):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        req = httpx.Request("POST", url)
        return httpx.Response(
            200,
            json={"developer_id": "dev-second", "status": "claimed"},
            request=req,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/api/bots/developer-ids/attach",
            json={"agent_api_key": secret_agent_key},
        )

    assert resp.status_code == 200
    assert resp.json() == {"developer_id": "dev-second", "status": "claimed"}
    assert captured["url"].endswith("/v1/accounts/developer-ids/attach")
    assert captured["headers"]["Authorization"] == "Bearer ate_caller_secret"
    assert captured["json"] == {"agent_api_key": secret_agent_key}
    assert secret_agent_key not in caplog.text
    assert "ate_caller_secret" not in caplog.text


@pytest.mark.parametrize(
    ("exchange_code", "expected_status"),
    [
        (401, 403),
        (403, 403),
        (404, 404),
        (409, 409),
    ],
)
def test_attach_developer_id_error_mapping_and_secret_redaction(
    monkeypatch, caplog, exchange_code, expected_status
):
    user = _user(key="ate_caller_secret")
    client = _client(user)
    secret_agent_key = "ate_never_leak_me_12345"

    def fake_post(url, *, json=None, headers=None, timeout=10):
        req = httpx.Request("POST", url)
        return httpx.Response(
            exchange_code,
            json={"detail": f"Error involving {secret_agent_key}"},
            request=req,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    with caplog.at_level(logging.DEBUG):
        resp = client.post(
            "/api/bots/developer-ids/attach",
            json={"agent_api_key": secret_agent_key},
        )

    assert resp.status_code == expected_status
    assert secret_agent_key not in resp.text
    assert secret_agent_key not in caplog.text
    assert "ate_caller_secret" not in caplog.text


@pytest.mark.parametrize(
    ("exchange_code", "expected_status"),
    [
        (400, 400),
        (403, 403),
        (404, 404),
    ],
)
def test_detach_developer_id_error_mapping(monkeypatch, exchange_code, expected_status):
    user = _user()
    client = _client(user)

    def fake_delete(url, *, headers=None, timeout=10):
        req = httpx.Request("DELETE", url)
        return httpx.Response(
            exchange_code,
            json={"detail": f"detach error {exchange_code}"},
            request=req,
        )

    monkeypatch.setattr(httpx, "delete", fake_delete)
    resp = client.delete("/api/bots/developer-ids/dev-other")
    assert resp.status_code == expected_status


def test_detach_developer_id_happy_path(monkeypatch):
    user = _user(key="ate_owner")
    client = _client(user)
    captured: dict = {}

    def fake_delete(url, *, headers=None, timeout=10):
        captured["url"] = url
        captured["headers"] = headers
        req = httpx.Request("DELETE", url)
        return httpx.Response(
            200,
            json={"developer_id": "dev-second", "status": "detached"},
            request=req,
        )

    monkeypatch.setattr(httpx, "delete", fake_delete)
    resp = client.delete("/api/bots/developer-ids/dev-second")
    assert resp.status_code == 200
    assert resp.json()["status"] == "detached"
    assert captured["url"].endswith("/v1/accounts/developer-ids/dev-second")
    assert captured["headers"]["Authorization"] == "Bearer ate_owner"


def test_register_managed_bot_happy_path_does_not_mutate_user(monkeypatch):
    user = _user(bot_id="acct-owner", key="ate_owner_original")
    client = _client(user)
    captured: dict = {}

    def fake_post(url, *, json=None, headers=None, timeout=10):
        captured["url"] = url
        captured["json"] = json
        req = httpx.Request("POST", url)
        return httpx.Response(
            201,
            json={
                "message": "Bot registered successfully.",
                "account": {
                    "id": "acct-new-child",
                    "bot_name": "ChildSolver",
                    "developer_id": "dev-mine",
                    "status": "active",
                },
                "api_key": "ate_brand_new_child_key",
                "starter_tokens": 100,
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    resp = client.post(
        "/api/bots/register",
        json={
            "bot_name": "ChildSolver",
            "developer_id": "dev-mine",
            "description": "Helper solver",
            "skills": ["python", "security"],
            "is_test": True,
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {
        "account": {
            "id": "acct-new-child",
            "bot_name": "ChildSolver",
            "developer_id": "dev-mine",
            "status": "active",
        },
        "api_key": "ate_brand_new_child_key",
        "starter_tokens": 100,
    }
    assert captured["url"].endswith("/v1/accounts/register")
    assert captured["json"] == {
        "bot_name": "ChildSolver",
        "developer_id": "dev-mine",
        "developer_name": "Operator",
        "contact_email": "operator@example.com",
        "owner_api_key": "ate_owner_original",
        "description": "Helper solver",
        "skills": ["python", "security"],
        "is_test": True,
    }
    # User's own linked account & key MUST remain untouched
    assert user.exchange_bot_id == "acct-owner"
    assert user.exchange_api_key == "ate_owner_original"


@pytest.mark.parametrize(
    ("exchange_code", "expected_status"),
    [
        (403, 403),
        (409, 409),
        (422, 422),
    ],
)
def test_register_managed_bot_error_mapping(monkeypatch, exchange_code, expected_status):
    user = _user()
    client = _client(user)

    def fake_post(url, *, json=None, headers=None, timeout=10):
        req = httpx.Request("POST", url)
        return httpx.Response(
            exchange_code,
            json={"detail": f"register failure {exchange_code}"},
            request=req,
        )

    monkeypatch.setattr(httpx, "post", fake_post)
    resp = client.post(
        "/api/bots/register",
        json={"bot_name": "BadBot", "developer_id": "dev-foreign"},
    )
    assert resp.status_code == expected_status


def test_publish_card_happy_path_and_403_and_422_forwarding(monkeypatch):
    user = _user(key="ate_owner")
    client = _client(user)
    card_payload = {
        "protocol_version": "0.1",
        "name": "SibAgent",
        "id": "did:web:example.com:agents:sib",
        "url": "https://example.com/a2a",
    }

    def fake_put_ok(url, *, json=None, headers=None, timeout=10):
        assert url.endswith("/v1/accounts/acct-sib/card")
        assert headers["Authorization"] == "Bearer ate_owner"
        assert json == card_payload
        req = httpx.Request("PUT", url)
        return httpx.Response(
            200,
            json={"agent_id": "acct-sib", "kya_level_verified": 0, "card": card_payload},
            request=req,
        )

    monkeypatch.setattr(httpx, "put", fake_put_ok)
    resp_ok = client.put("/api/bots/acct-sib/card", json=card_payload)
    assert resp_ok.status_code == 200
    assert resp_ok.json()["agent_id"] == "acct-sib"

    # Cross-owner 403
    monkeypatch.setattr(
        exchange_svc,
        "publish_managed_agent_card",
        lambda *_a, **_k: (_ for _ in ()).throw(
            _http_error(403, {"detail": "Not authorized to update this Agent Card"})
        ),
    )
    resp_403 = client.put("/api/bots/acct-foreign/card", json=card_payload)
    assert resp_403.status_code == 403

    # Unknown 404
    monkeypatch.setattr(
        exchange_svc,
        "publish_managed_agent_card",
        lambda *_a, **_k: (_ for _ in ()).throw(
            _http_error(404, {"detail": "Account not found"})
        ),
    )
    resp_404 = client.put("/api/bots/acct-missing/card", json=card_payload)
    assert resp_404.status_code == 404

    # 422 forwards exchange validation detail verbatim
    validation_detail = [{"loc": ["url"], "msg": "Field required", "type": "missing"}]
    monkeypatch.setattr(
        exchange_svc,
        "publish_managed_agent_card",
        lambda *_a, **_k: (_ for _ in ()).throw(
            _http_error(422, {"detail": validation_detail})
        ),
    )
    resp_422 = client.put("/api/bots/acct-sib/card", json={"bad": True})
    assert resp_422.status_code == 422
    assert resp_422.json()["detail"] == validation_detail


def test_settlements_happy_path_and_cross_owner_403(monkeypatch):
    user = _user(key="ate_owner")
    client = _client(user)

    def fake_get_ok(url, *, headers=None, timeout=10):
        assert url.endswith("/v1/accounts/acct-sib/settlements")
        assert headers["Authorization"] == "Bearer ate_owner"
        req = httpx.Request("GET", url)
        return httpx.Response(
            200,
            json={
                "account_id": "acct-sib",
                "settlements": [
                    {
                        "id": "tx-1",
                        "tx_type": "escrow_release",
                        "amount": 250,
                        "direction": "in",
                        "escrow_id": "esc-1",
                        "created_at": "2026-11-15T10:00:00Z",
                    }
                ],
            },
            request=req,
        )

    monkeypatch.setattr(httpx, "get", fake_get_ok)
    resp_ok = client.get("/api/bots/acct-sib/settlements")
    assert resp_ok.status_code == 200
    assert len(resp_ok.json()["settlements"]) == 1

    monkeypatch.setattr(
        exchange_svc,
        "get_managed_agent_settlements",
        lambda *_a, **_k: (_ for _ in ()).throw(_http_error(403)),
    )
    assert client.get("/api/bots/acct-foreign/settlements").status_code == 403

    monkeypatch.setattr(
        exchange_svc,
        "get_managed_agent_settlements",
        lambda *_a, **_k: (_ for _ in ()).throw(_http_error(404)),
    )
    assert client.get("/api/bots/acct-missing/settlements").status_code == 404


# ---------------------------------------------------------------------------
# Directory evidenced flag on GET /api/agents
# ---------------------------------------------------------------------------


def test_directory_agents_evidenced_flag_bulk_query(monkeypatch):
    monkeypatch.setattr(
        exchange_svc,
        "get_directory",
        lambda: {
            "bots": [
                {"id": "bot-evidenced", "bot_name": "Pro", "skills": ["python"]},
                {"id": "bot-few", "bot_name": "Newbie", "skills": ["python"]},
                {"id": "bot-wrong-skill", "bot_name": "Mismatch", "skills": ["rust"]},
            ]
        },
    )

    db_rows = [
        # bot-evidenced: 5 settled python bounties with avg 84.0 -> evidenced True
        ("bot-evidenced", ["python"], 80, None),
        ("bot-evidenced", ["python"], 85, None),
        ("bot-evidenced", ["python"], None, {"score": 90}),
        ("bot-evidenced", ["python", "api"], 80, None),
        ("bot-evidenced", ["python"], 85, None),
        # bot-few: only 2 settled bounties -> evidenced False
        ("bot-few", ["python"], 95, None),
        ("bot-few", ["python"], 90, None),
        # bot-wrong-skill: 5 settled python bounties, but declared skill is rust -> evidenced False
        ("bot-wrong-skill", ["python"], 90, None),
        ("bot-wrong-skill", ["python"], 90, None),
        ("bot-wrong-skill", ["python"], 90, None),
        ("bot-wrong-skill", ["python"], 90, None),
        ("bot-wrong-skill", ["python"], 90, None),
    ]
    execute_calls = 0

    class _FakeDb:
        async def execute(self, _stmt):
            nonlocal execute_calls
            execute_calls += 1
            return SimpleNamespace(all=lambda: db_rows)

    app = FastAPI()
    app.include_router(agents_routes.router, prefix="/api/agents")

    async def override_db():
        yield _FakeDb()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    resp = client.get("/api/agents")
    assert resp.status_code == 200
    assert execute_calls == 1  # single bulk query
    by_id = {a["id"]: a for a in resp.json()["agents"]}
    assert by_id["bot-evidenced"]["evidenced"] is True
    assert by_id["bot-few"]["evidenced"] is False
    assert by_id["bot-wrong-skill"]["evidenced"] is False


def test_directory_agents_evidenced_fails_open_on_db_error(monkeypatch):
    monkeypatch.setattr(
        exchange_svc,
        "get_directory",
        lambda: {"bots": [{"id": "bot-1", "bot_name": "One", "skills": ["python"]}]},
    )

    class _BrokenDb:
        async def execute(self, _stmt):
            raise RuntimeError("db connection lost")

    app = FastAPI()
    app.include_router(agents_routes.router, prefix="/api/agents")

    async def override_db():
        yield _BrokenDb()

    app.dependency_overrides[get_db] = override_db
    client = TestClient(app)

    resp = client.get("/api/agents")
    assert resp.status_code == 200
    assert resp.json()["agents"] == [
        {"id": "bot-1", "bot_name": "One", "skills": ["python"], "evidenced": False}
    ]
