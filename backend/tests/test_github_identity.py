"""GitHub identity OAuth and routes. No database and no network."""

# ruff: noqa: E402

from __future__ import annotations

import asyncio
import sys
import time
import types
import uuid
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

# The specified interpreter does not have passlib. The route imports get_current_user,
# and that module constructs a CryptContext at import time. Stub it before the import.
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
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.middleware.auth import get_current_user
from app.models.github import GitHubIdentity
from app.routes.github_identity import router
from app.services import github_identity
from app.services.github_identity import (
    IdentityConflictError,
    build_authorize_url,
    complete_oauth,
    get_identity_for_user,
)

CLIENT_ID = "cid-123"
CLIENT_SECRET = "csecret"
OAUTH_URL = "https://github.example"
API_URL = "https://api.github.example"
MARKET_URL = "https://market.example/"
JWT_SECRET = "state-secret"
USER_TOKEN = "ghu_test_token"
OAUTH_CODE = "oauth-code-value"
STATE_USER = uuid.uuid4()


def _run(coro):
    return asyncio.run(coro)


class _Result:
    def __init__(self, row: GitHubIdentity | None):
        self._row = row

    def scalar_one_or_none(self) -> GitHubIdentity | None:
        return self._row


class IdentitySession:
    def __init__(self, rows: list[GitHubIdentity] | None = None):
        self.rows = list(rows or [])
        self.added: list[object] = []
        self.deleted: list[object] = []
        self.commits = 0
        self.rollbacks = 0

    def add(self, obj: object) -> None:
        self.added.append(obj)
        self.rows.append(obj)

    async def delete(self, obj: object) -> None:
        self.deleted.append(obj)
        self.rows = [row for row in self.rows if row is not obj]

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def execute(self, statement):
        clause = statement.whereclause
        if clause is None or not hasattr(clause.left, "key"):
            raise AssertionError(f"unexpected statement {statement}")
        column = clause.left.key
        value = clause.right.value
        matches = [row for row in self.rows if getattr(row, column) == value]
        if len(matches) > 1:
            raise AssertionError("multiple rows")
        return _Result(matches[0] if matches else None)


class _User:
    def __init__(self, user_id: uuid.UUID):
        self.id = user_id


@pytest.fixture(autouse=True)
def _oauth_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(github_identity.settings, "GITHUB_OAUTH_CLIENT_ID", CLIENT_ID)
    monkeypatch.setattr(github_identity.settings, "GITHUB_OAUTH_CLIENT_SECRET", CLIENT_SECRET)
    monkeypatch.setattr(github_identity.settings, "GITHUB_OAUTH_URL", OAUTH_URL)
    monkeypatch.setattr(github_identity.settings, "GITHUB_API_URL", API_URL)
    monkeypatch.setattr(github_identity.settings, "MARKET_URL", MARKET_URL)
    monkeypatch.setattr(github_identity.settings, "JWT_SECRET", JWT_SECRET)
    monkeypatch.setattr(github_identity.settings, "JWT_ALGORITHM", "HS256")

    def refuse() -> httpx.AsyncClient:
        raise AssertionError("unexpected HTTP client")

    monkeypatch.setattr(github_identity, "_client", refuse)


def _state_from_url(url: str) -> str:
    return parse_qs(urlparse(url).query)["state"][0]


def _decode_state(state: str) -> dict:
    return jwt.decode(
        state,
        JWT_SECRET,
        algorithms=["HS256"],
        options={"verify_aud": False, "require_exp": True},
    )


def _signed_state(user_id: uuid.UUID, **overrides) -> str:
    payload = {
        "sub": str(user_id),
        "purpose": "github_identity",
        "nonce": "nonce-value",
        "exp": int(time.time()) + 600,
    }
    payload.update(overrides)
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> list[httpx.Request]:
    calls: list[httpx.Request] = []

    def wrapped(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return handler(request)

    transport = httpx.MockTransport(wrapped)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport)

    monkeypatch.setattr(github_identity, "_client", factory)
    return calls


def _happy_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/login/oauth/access_token":
        assert request.method == "POST"
        assert request.headers["accept"] == "application/json"
        body = parse_qs(request.content.decode())
        assert body["client_id"] == [CLIENT_ID]
        assert body["client_secret"] == [CLIENT_SECRET]
        assert body["code"] == [OAUTH_CODE]
        return httpx.Response(200, json={"access_token": USER_TOKEN, "token_type": "bearer"})
    if request.url.path == "/user":
        assert request.headers["authorization"] == f"Bearer {USER_TOKEN}"
        return httpx.Response(200, json={"id": 42, "login": "octocat"})
    raise AssertionError(str(request.url))


def _identity(
    user_id: uuid.UUID, github_user_id: int = 42, login: str = "octocat"
) -> GitHubIdentity:
    return GitHubIdentity(
        user_id=user_id,
        github_user_id=github_user_id,
        login=login,
        verified_at=datetime(2020, 1, 1, tzinfo=timezone.utc),
    )


def test_authorize_url_params_and_state_claims():
    user_id = uuid.uuid4()
    before = time.time()
    url = build_authorize_url(user_id)
    after = time.time()
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "github.example"
    assert parsed.path == "/login/oauth/authorize"
    query = parse_qs(parsed.query)
    assert query["client_id"] == [CLIENT_ID]
    assert query["allow_signup"] == ["false"]
    assert "client_secret" not in url
    assert CLIENT_SECRET not in url
    payload = _decode_state(query["state"][0])
    assert payload["sub"] == str(user_id)
    assert payload["purpose"] == "github_identity"
    assert isinstance(payload["nonce"], str) and payload["nonce"]
    assert before + 590 <= payload["exp"] <= after + 600
    other = build_authorize_url(user_id)
    assert _state_from_url(other) != query["state"][0]


def test_missing_client_id_rejects_authorize_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(github_identity.settings, "GITHUB_OAUTH_CLIENT_ID", "  ")
    with pytest.raises(ValueError):
        build_authorize_url(uuid.uuid4())


def test_missing_jwt_secret_rejects_authorize_url(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(github_identity.settings, "JWT_SECRET", "")
    with pytest.raises(ValueError):
        build_authorize_url(uuid.uuid4())


def test_tampered_state_is_rejected():
    header, body, signature = _signed_state(uuid.uuid4()).split(".")
    # Flip a payload character. The final signature character can be ambiguous in base64.
    flipped_body = ("a" if body[0] != "a" else "b") + body[1:]
    flipped = f"{header}.{flipped_body}.{signature}"
    with pytest.raises(ValueError):
        _run(complete_oauth(IdentitySession(), OAUTH_CODE, flipped, user_id=STATE_USER))


def test_state_signed_with_wrong_secret_is_rejected():
    user_id = uuid.uuid4()
    payload = {
        "sub": str(user_id),
        "purpose": "github_identity",
        "nonce": "nonce-value",
        "exp": int(time.time()) + 600,
    }
    state = jwt.encode(payload, "other-secret", algorithm="HS256")
    with pytest.raises(ValueError):
        _run(complete_oauth(IdentitySession(), OAUTH_CODE, state, user_id=STATE_USER))


def test_expired_state_is_rejected():
    state = _signed_state(uuid.uuid4(), exp=int(time.time()) - 10)
    with pytest.raises(ValueError):
        _run(complete_oauth(IdentitySession(), OAUTH_CODE, state, user_id=STATE_USER))


def test_wrong_purpose_is_rejected():
    state = _signed_state(uuid.uuid4(), purpose="password_reset")
    with pytest.raises(ValueError):
        _run(complete_oauth(IdentitySession(), OAUTH_CODE, state, user_id=STATE_USER))


def test_oauth_happy_path_upserts_identity(monkeypatch: pytest.MonkeyPatch):
    calls = _install(monkeypatch, _happy_handler)
    user_id = uuid.uuid4()
    session = IdentitySession()
    identity = _run(complete_oauth(session, OAUTH_CODE, _signed_state(user_id), user_id=user_id))
    assert identity.user_id == user_id
    assert identity.github_user_id == 42
    assert identity.login == "octocat"
    assert identity.verified_at is not None
    assert identity.verified_at.tzinfo is not None
    assert session.added == [identity]
    assert session.commits == 0
    assert len(calls) == 2
    stored = repr(identity.__dict__)
    assert USER_TOKEN not in stored
    assert OAUTH_CODE not in stored
    assert CLIENT_SECRET not in stored
    assert _run(get_identity_for_user(session, user_id)) is identity
    assert _run(get_identity_for_user(session, uuid.uuid4())) is None


def test_relink_updates_existing_identity(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, _happy_handler)
    user_id = uuid.uuid4()
    existing = _identity(user_id, github_user_id=7, login="old-name")
    previous = existing.verified_at
    session = IdentitySession([existing])
    identity = _run(complete_oauth(session, OAUTH_CODE, _signed_state(user_id), user_id=user_id))
    assert identity is existing
    assert existing.github_user_id == 42
    assert existing.login == "octocat"
    assert existing.verified_at > previous
    assert session.added == []
    assert len(session.rows) == 1


def test_conflict_when_github_id_linked_to_other_user(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, _happy_handler)
    owner = uuid.uuid4()
    existing = _identity(owner, github_user_id=42, login="taken")
    session = IdentitySession([existing])
    with pytest.raises(IdentityConflictError):
        _run(complete_oauth(session, OAUTH_CODE, _signed_state(STATE_USER), user_id=STATE_USER))
    assert existing.login == "taken"
    assert existing.github_user_id == 42
    assert session.added == []
    assert session.commits == 0


def test_missing_client_secret_does_not_call_github(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(github_identity.settings, "GITHUB_OAUTH_CLIENT_SECRET", "")
    with pytest.raises(ValueError):
        _run(complete_oauth(IdentitySession(), OAUTH_CODE, _signed_state(STATE_USER), user_id=STATE_USER))


@pytest.mark.parametrize(
    "token_response",
    [
        httpx.Response(200, json={"error": "bad_verification_code"}),
        httpx.Response(400, json={"error": "incorrect_client_credentials"}),
        httpx.Response(200, text="not-json"),
        httpx.Response(200, json={"token_type": "bearer"}),
    ],
)
def test_github_token_errors_raise_value_error(monkeypatch: pytest.MonkeyPatch, token_response):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/login/oauth/access_token"
        return token_response

    _install(monkeypatch, handler)
    with pytest.raises(ValueError) as caught:
        _run(complete_oauth(IdentitySession(), OAUTH_CODE, _signed_state(STATE_USER), user_id=STATE_USER))
    assert OAUTH_CODE not in str(caught.value)
    assert CLIENT_SECRET not in str(caught.value)


@pytest.mark.parametrize(
    "user_body",
    [
        {"id": "42", "login": "octocat"},
        {"id": True, "login": "octocat"},
        {"id": 0, "login": "octocat"},
        {"id": 42, "login": ""},
        {"id": 42},
    ],
)
def test_github_user_errors_raise_value_error(monkeypatch: pytest.MonkeyPatch, user_body: dict):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": USER_TOKEN, "token_type": "bearer"})
        return httpx.Response(200, json=user_body)

    _install(monkeypatch, handler)
    session = IdentitySession()
    with pytest.raises(ValueError) as caught:
        _run(complete_oauth(session, OAUTH_CODE, _signed_state(STATE_USER), user_id=STATE_USER))
    assert session.added == []
    assert USER_TOKEN not in str(caught.value)


def test_github_user_http_error_raises_value_error(monkeypatch: pytest.MonkeyPatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/login/oauth/access_token":
            return httpx.Response(200, json={"access_token": USER_TOKEN, "token_type": "bearer"})
        return httpx.Response(401, json={"message": "Bad credentials"})

    _install(monkeypatch, handler)
    with pytest.raises(ValueError):
        _run(complete_oauth(IdentitySession(), OAUTH_CODE, _signed_state(STATE_USER), user_id=STATE_USER))


def _app(session: IdentitySession, user: _User | None) -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1/github/identity")

    async def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    if user is not None:
        app.dependency_overrides[get_current_user] = lambda: user
    return app


def _client(session: IdentitySession, user: _User | None) -> TestClient:
    return TestClient(_app(session, user), follow_redirects=False)


CALLBACK = "/api/v1/github/identity/callback"


def test_callback_links_signed_in_user(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, _happy_handler)
    user = _User(uuid.uuid4())
    session = IdentitySession()
    with _client(session, user) as client:
        linked = client.post(
            CALLBACK, json={"code": OAUTH_CODE, "state": _signed_state(user.id)}
        )
    assert linked.status_code == 200
    assert linked.json()["linked"] is True
    assert linked.json()["login"] == "octocat"
    assert session.commits == 1
    assert session.rows[0].user_id == user.id


def test_callback_requires_auth(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, _happy_handler)
    session = IdentitySession()
    with _client(session, None) as client:
        response = client.post(
            CALLBACK, json={"code": OAUTH_CODE, "state": _signed_state(uuid.uuid4())}
        )
    assert response.status_code == 401
    assert session.added == []


def test_callback_rejects_state_minted_for_another_user(monkeypatch: pytest.MonkeyPatch):
    calls = _install(monkeypatch, _happy_handler)
    attacker = uuid.uuid4()
    victim = _User(uuid.uuid4())
    session = IdentitySession()
    with _client(session, victim) as client:
        response = client.post(
            CALLBACK, json={"code": OAUTH_CODE, "state": _signed_state(attacker)}
        )
    assert response.status_code == 400
    assert session.added == []
    assert session.commits == 0
    assert calls == []


def test_callback_conflict_and_error(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, _happy_handler)
    user = _User(uuid.uuid4())
    other = IdentitySession([_identity(uuid.uuid4(), github_user_id=42, login="taken")])
    with _client(other, user) as client:
        conflict = client.post(
            CALLBACK, json={"code": OAUTH_CODE, "state": _signed_state(user.id)}
        )
    assert conflict.status_code == 409
    assert other.commits == 0
    assert other.rows[0].login == "taken"

    errored = IdentitySession()
    with _client(errored, user) as client:
        error = client.post(CALLBACK, json={"code": OAUTH_CODE, "state": "not-a-token"})
    assert error.status_code == 400
    assert errored.commits == 0
    assert errored.added == []


def test_callback_unique_race_is_conflict(monkeypatch: pytest.MonkeyPatch):
    _install(monkeypatch, _happy_handler)
    user = _User(uuid.uuid4())

    class RacingSession(IdentitySession):
        async def commit(self) -> None:
            raise IntegrityError("insert", {}, Exception("duplicate github_user_id"))

    session = RacingSession()
    with _client(session, user) as client:
        response = client.post(
            CALLBACK, json={"code": OAUTH_CODE, "state": _signed_state(user.id)}
        )
    assert response.status_code == 409
    assert session.rollbacks == 1


def test_connect_status_and_delete():
    user = _User(uuid.uuid4())
    verified = datetime(2026, 11, 5, 15, 0, tzinfo=timezone.utc)
    identity = GitHubIdentity(
        user_id=user.id,
        github_user_id=42,
        login="octocat",
        verified_at=verified,
    )
    session = IdentitySession([identity])
    with _client(session, user) as client:
        connect = client.get("/api/v1/github/identity/connect")
        assert connect.status_code == 200
        authorize_url = connect.json()["authorize_url"]
        assert parse_qs(urlparse(authorize_url).query)["client_id"] == [CLIENT_ID]
        assert _decode_state(_state_from_url(authorize_url))["sub"] == str(user.id)

        status = client.get("/api/v1/github/identity/")
        assert status.status_code == 200
        assert status.json() == {
            "linked": True,
            "login": "octocat",
            "github_user_id": 42,
            "verified_at": verified.isoformat(),
        }

        deleted = client.delete("/api/v1/github/identity/")
        assert deleted.status_code == 200
        assert deleted.json() == {"linked": False}
        assert session.deleted == [identity]
        assert session.commits == 1
        assert identity not in session.rows

        unlinked = client.get("/api/v1/github/identity/")
        assert unlinked.json() == {
            "linked": False,
            "login": None,
            "github_user_id": None,
            "verified_at": None,
        }


def test_delete_when_unlinked_returns_false():
    user = _User(uuid.uuid4())
    session = IdentitySession()
    with _client(session, user) as client:
        response = client.delete("/api/v1/github/identity/")
    assert response.status_code == 200
    assert response.json() == {"linked": False}
    assert session.deleted == []
    assert session.commits == 1


def test_connect_requires_auth_and_reports_unconfigured(monkeypatch: pytest.MonkeyPatch):
    session = IdentitySession()
    with _client(session, None) as client:
        missing = client.get("/api/v1/github/identity/connect")
    assert missing.status_code == 401

    monkeypatch.setattr(github_identity.settings, "GITHUB_OAUTH_CLIENT_ID", "")
    with _client(session, _User(uuid.uuid4())) as client:
        unconfigured = client.get("/api/v1/github/identity/connect")
    assert unconfigured.status_code == 503
    assert "authorize_url" not in unconfigured.json()
