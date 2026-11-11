"""GitHub Actions OIDC verification. Keys are local. JWKS fetch is monkeypatched."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import long_to_base64

from app.config import settings
from app.services import github_oidc
from app.services.github_oidc import (
    GITHUB_ACTIONS_ISSUER,
    JWKS_URL,
    GitHubActionsClaims,
    GitHubOidcError,
    clear_jwks_cache,
    fetch_jwks,
    unverified_issuer,
    verify_github_actions_token,
)

SHA = "b" * 40
KID = "oidc-test-1"
AUDIENCE = "settlebridge"
_PRIVATE: str | None = None
_JWK: dict | None = None


def _b64_int(value: int) -> str:
    encoded = long_to_base64(value)
    if isinstance(encoded, bytes):
        return encoded.decode("ascii")
    return encoded


def _material() -> tuple[str, dict]:
    global _PRIVATE, _JWK
    if _PRIVATE is None or _JWK is None:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        private = key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("ascii")
        numbers = key.public_key().public_numbers()
        _PRIVATE = private
        _JWK = {
            "kty": "RSA",
            "use": "sig",
            "alg": "RS256",
            "kid": KID,
            "n": _b64_int(numbers.n),
            "e": _b64_int(numbers.e),
        }
    return _PRIVATE, dict(_JWK)


def public_jwk_set() -> dict:
    _, jwk = _material()
    return {"keys": [jwk]}


def _segment(data: dict) -> str:
    raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _encode(claims: dict, key: str, *, algorithm: str, kid: str, headers: dict | None) -> str:
    token_headers = {"kid": kid}
    if headers:
        token_headers.update(headers)
    encoded = jwt.encode(claims, key, algorithm=algorithm, headers=token_headers)
    if isinstance(encoded, bytes):
        return encoded.decode("ascii")
    return encoded


def actions_token(
    *,
    repository: str = "Acme/Widget",
    repository_owner: str = "Acme",
    repository_id: str | int = "4242",
    sha: str = SHA,
    event_name: str = "push",
    audience: str = AUDIENCE,
    issuer: str = GITHUB_ACTIONS_ISSUER,
    exp_offset: int = 300,
    nbf_offset: int = 0,
    iat_offset: int = 0,
    kid: str = KID,
    alg: str = "RS256",
    omit: tuple[str, ...] = (),
    extra: dict | None = None,
    headers: dict | None = None,
) -> str:
    """Sign a GitHub Actions-shaped JWT with the test key, or a rejected alg."""
    now = int(time.time())
    claims: dict = {
        "iss": issuer,
        "aud": audience,
        "iat": now + iat_offset,
        "nbf": now + nbf_offset,
        "exp": now + exp_offset,
        "sub": f"repo:{repository}:ref:refs/heads/main",
        "repository": repository,
        "repository_id": repository_id,
        "repository_owner": repository_owner,
        "sha": sha,
        "event_name": event_name,
    }
    for name in omit:
        claims.pop(name, None)
    if extra:
        claims.update(extra)
    if alg == "RS256":
        private, _ = _material()
        return _encode(claims, private, algorithm="RS256", kid=kid, headers=headers)
    if alg == "HS256":
        return _encode(claims, "not-the-app-secret", algorithm="HS256", kid=kid, headers=headers)
    header = {"alg": alg, "typ": "JWT", "kid": kid}
    if headers:
        header.update(headers)
    return _segment(header) + "." + _segment(claims) + "."


def install_jwks(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Serve the test public key from a fake JWKS fetch. No network."""
    clear_jwks_cache()
    state: dict = {"calls": [], "document": public_jwk_set()}

    async def fetch(url: str) -> dict:
        state["calls"].append(url)
        if url != JWKS_URL:
            raise AssertionError(url)
        return state["document"]

    monkeypatch.setattr(github_oidc, "fetch_jwks", fetch)
    return state


def _verify(token: str) -> GitHubActionsClaims:
    return asyncio.run(verify_github_actions_token(token))


@pytest.fixture(autouse=True)
def _reset_oidc_cache():
    clear_jwks_cache()
    yield
    clear_jwks_cache()


@pytest.fixture
def oidc_settings(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_OIDC_AUDIENCE", AUDIENCE)
    monkeypatch.setattr(settings, "FIX_VERIFIER_OIDC_JWKS_TTL_SECONDS", 3600)
    return monkeypatch


def test_valid_token_returns_the_required_claims(oidc_settings, monkeypatch):
    install_jwks(monkeypatch)
    claims = _verify(actions_token(sha="B" * 40, repository_id=99))
    assert claims == GitHubActionsClaims(
        repository="Acme/Widget",
        repository_id="99",
        repository_owner="Acme",
        sha=SHA,
        event_name="push",
    )


def test_jwks_cache_is_reused_until_the_kid_is_unknown(oidc_settings, monkeypatch):
    state = install_jwks(monkeypatch)
    token = actions_token()
    _verify(token)
    _verify(token)
    assert state["calls"] == [JWKS_URL]


def test_expired_cache_is_refetched(oidc_settings, monkeypatch):
    monkeypatch.setattr(settings, "FIX_VERIFIER_OIDC_JWKS_TTL_SECONDS", 0)
    state = install_jwks(monkeypatch)
    token = actions_token()
    _verify(token)
    _verify(token)
    assert state["calls"] == [JWKS_URL, JWKS_URL]


def test_unknown_kid_refreshes_once_and_can_find_the_new_key(oidc_settings, monkeypatch):
    clear_jwks_cache()
    other = public_jwk_set()["keys"][0].copy()
    other["kid"] = "other-key"
    docs = [{"keys": [other]}, public_jwk_set()]
    calls: list[str] = []

    async def fetch(url: str) -> dict:
        calls.append(url)
        return docs.pop(0) if len(docs) > 1 else docs[0]

    monkeypatch.setattr(github_oidc, "fetch_jwks", fetch)
    claims = _verify(actions_token())
    assert claims.sha == SHA
    assert calls == [JWKS_URL, JWKS_URL]


def test_unknown_kid_is_rejected_after_one_refresh(oidc_settings, monkeypatch):
    state = install_jwks(monkeypatch)
    other = public_jwk_set()["keys"][0].copy()
    other["kid"] = "other-key"
    state["document"] = {"keys": [other]}
    with pytest.raises(GitHubOidcError) as caught:
        _verify(actions_token())
    assert state["calls"] == [JWKS_URL, JWKS_URL]
    assert "oidc-test" not in str(caught.value)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: actions_token(audience="https://evil.example"),
        lambda: actions_token(issuer="https://evil.example"),
        lambda: actions_token(exp_offset=-120),
        lambda: actions_token(nbf_offset=120),
        lambda: actions_token(iat_offset=120),
        lambda: actions_token(extra={"aud": [AUDIENCE, "other"]}),
        lambda: actions_token(omit=("repository",)),
        lambda: actions_token(repository_owner="other"),
        lambda: actions_token(headers={"crit": ["b64"]}),
        lambda: actions_token(headers={"jwk": {"kty": "RSA"}}),
    ],
)
def test_bad_claims_and_headers_are_rejected(oidc_settings, monkeypatch, factory):
    install_jwks(monkeypatch)
    token = factory()
    with pytest.raises(GitHubOidcError) as caught:
        _verify(token)
    assert token not in str(caught.value)
    cause = caught.value.__cause__
    if cause is not None:
        assert token not in str(cause)


@pytest.mark.parametrize("alg", ["none", "None", "HS256"])
def test_disallowed_algorithms_do_not_fetch_jwks(oidc_settings, monkeypatch, alg):
    state = install_jwks(monkeypatch)
    token = actions_token(alg=alg)
    with pytest.raises(GitHubOidcError) as caught:
        _verify(token)
    assert state["calls"] == []
    assert token not in str(caught.value)


def test_errors_and_logs_do_not_contain_the_token(oidc_settings, monkeypatch, caplog):
    install_jwks(monkeypatch)
    token = actions_token(audience="other-audience")
    caplog.set_level(logging.DEBUG)
    with pytest.raises(GitHubOidcError) as caught:
        _verify(token)
    assert token not in str(caught.value)
    assert token not in caplog.text
    assert token not in repr(caught.value.__cause__)


def test_unverified_issuer_is_only_a_routing_hint():
    assert unverified_issuer(actions_token()) == GITHUB_ACTIONS_ISSUER
    assert unverified_issuer(actions_token(issuer="https://evil.example")) == "https://evil.example"
    assert unverified_issuer("not-a-jwt") is None
    assert unverified_issuer(actions_token() + " trailing") is None


def test_fetch_rejects_any_url_other_than_the_github_jwks():
    with pytest.raises(GitHubOidcError) as caught:
        asyncio.run(fetch_jwks("https://evil.example/.well-known/jwks"))
    assert "evil.example" not in str(caught.value)
