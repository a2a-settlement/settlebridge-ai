"""Scoped installation tokens. No network — httpx.MockTransport only."""

from __future__ import annotations

import asyncio
import json
import sys
import types

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

from app.services import github_app, github_scoped_token  # noqa: E402
from app.services.github_app import GitHubAPIError  # noqa: E402

API = "https://api.github.com"
APP_ID = "8675309"
SECRET = "ghs_broader_SHOULD_NOT_LEAK"


def _rsa_pair() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode("ascii")
    public = key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, public.decode("ascii")


_PRIVATE_PEM, _PUBLIC_PEM = _rsa_pair()


def _configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(github_app.settings, "GITHUB_APP_ID", APP_ID)
    monkeypatch.setattr(github_app.settings, "GITHUB_APP_PRIVATE_KEY", _PRIVATE_PEM)
    monkeypatch.setattr(github_app.settings, "GITHUB_API_URL", API)


def _install(monkeypatch: pytest.MonkeyPatch, handler) -> None:
    transport = httpx.MockTransport(handler)

    def factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=30)

    monkeypatch.setattr(github_scoped_token, "_client", factory)


def _narrow_body(token: str = "ghs_narrow") -> dict:
    return {
        "token": token,
        "expires_at": "2026-11-07T12:00:00Z",
        "permissions": {"contents": "read"},
        "repository_selection": "selected",
    }


def _assert_request(request: httpx.Request, installation_id: int, repo_name: str) -> None:
    assert request.method == "POST"
    assert request.url.path == f"/app/installations/{installation_id}/access_tokens"
    assert json.loads(request.content) == {
        "repositories": [repo_name],
        "permissions": {"contents": "read"},
    }
    scheme, app_jwt = request.headers["authorization"].split(" ", 1)
    assert scheme == "Bearer"
    assert jwt.get_unverified_header(app_jwt)["alg"] == "RS256"
    claims = jwt.decode(app_jwt, _PUBLIC_PEM, algorithms=["RS256"], options={"verify_exp": False})
    assert claims["iss"] == APP_ID


def test_requests_exact_body_with_app_jwt(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        _assert_request(request, 42, "widgets")
        return httpx.Response(201, json=_narrow_body())

    _install(monkeypatch, handler)
    minted = asyncio.run(github_scoped_token.scoped_read_token(42, "acme/widgets"))
    assert minted == "ghs_narrow"
    assert len(seen) == 1
    assert request_repositories(seen[0]) == ["widgets"]


def test_accepts_metadata_read_and_matching_repository(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch)
    body = _narrow_body()
    body["permissions"] = {"contents": "read", "metadata": "read"}
    body["repositories"] = [{"full_name": "acme/widgets"}]
    _install(monkeypatch, lambda request: httpx.Response(201, json=body))
    assert asyncio.run(github_scoped_token.scoped_read_token(42, "acme/widgets")) == "ghs_narrow"


def request_repositories(request: httpx.Request) -> list:
    return json.loads(request.content)["repositories"]


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"permissions": {"contents": "read", "metadata": "write"}}, "not scoped"),
        ({"permissions": {"contents": "read", "issues": "read"}}, "not scoped"),
        ({"permissions": {"contents": "write"}}, "not scoped"),
        ({"repositories": [{"full_name": "acme/other"}]}, "other repositories"),
        (
            {"repositories": [{"full_name": "acme/widgets"}, {"full_name": "acme/other"}]},
            "other repositories",
        ),
        ({"repositories": "acme/widgets"}, "other repositories"),
        ({"permissions": {"metadata": "read"}}, "not scoped"),
        ({"repository_selection": "all"}, "not scoped"),
        ({"repository_selection": "Selected"}, "not scoped"),
        ({"token": ""}, "incomplete"),
        ({"token": None}, "incomplete"),
        ({"token": 15}, "incomplete"),
    ],
)
def test_rejects_broader_or_incomplete_token(monkeypatch: pytest.MonkeyPatch, overrides, match):
    _configure(monkeypatch)
    body = _narrow_body(SECRET)
    body.update(overrides)

    def handler(request: httpx.Request) -> httpx.Response:
        _assert_request(request, 42, "widgets")
        return httpx.Response(201, json=body)

    _install(monkeypatch, handler)
    with pytest.raises(GitHubAPIError, match=match) as excinfo:
        asyncio.run(github_scoped_token.scoped_read_token(42, "acme/widgets"))
    assert SECRET not in str(excinfo.value)
    assert excinfo.value.status_code == 201


def test_non_object_token_response_is_incomplete(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json=["nope"])

    _install(monkeypatch, handler)
    with pytest.raises(GitHubAPIError, match="incomplete") as excinfo:
        asyncio.run(github_scoped_token.scoped_read_token(9, "acme/widgets"))
    assert excinfo.value.status_code == 201


def test_does_not_cache_tokens(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch)
    before = dict(github_app._token_cache)
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        _assert_request(request, 7, "widgets")
        return httpx.Response(201, json=_narrow_body(f"ghs_{len(seen)}"))

    _install(monkeypatch, handler)

    async def scenario() -> tuple[str, str]:
        first = await github_scoped_token.scoped_read_token(7, "acme/widgets")
        second = await github_scoped_token.scoped_read_token(7, "acme/widgets")
        return first, second

    assert asyncio.run(scenario()) == ("ghs_1", "ghs_2")
    assert seen == [
        {"repositories": ["widgets"], "permissions": {"contents": "read"}},
        {"repositories": ["widgets"], "permissions": {"contents": "read"}},
    ]
    assert github_app._token_cache == before


def test_invalid_repository_does_not_call_github(monkeypatch: pytest.MonkeyPatch):
    def boom() -> None:
        raise AssertionError("GitHub should not be called")

    monkeypatch.setattr(github_scoped_token, "_client", boom)
    monkeypatch.setattr(github_scoped_token, "_app_jwt", boom)
    with pytest.raises(GitHubAPIError, match="invalid repository"):
        asyncio.run(github_scoped_token.scoped_read_token(1, "not a repo"))
