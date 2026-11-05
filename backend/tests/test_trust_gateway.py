"""Local mcp-trust-gateway token verification. No database and no network."""

# ruff: noqa: E402

from __future__ import annotations

import base64
import json
import sys
import time
import types

sys.modules.setdefault("asyncpg", types.ModuleType("asyncpg"))

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt

from app.services import trust_gateway
from app.services.trust_gateway import (
    CLAIMS_NAMESPACE,
    TrustTokenError,
    bearer_token,
    trust_denial,
    verify_gateway_token,
)

ISSUER = "https://gateway.a2a-settlement.org"
AUDIENCE = "https://gateway.a2a-settlement.org"
HMAC_SECRET = "hmac-secret-for-tests"


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
    ).decode("ascii")
    return private, public


_PRIVATE_PEM, _PUBLIC_PEM = _rsa_pair()


def _configure(
    monkeypatch: pytest.MonkeyPatch,
    *,
    key: str,
    algorithms: str,
    issuer: str = ISSUER,
    audience: str = AUDIENCE,
) -> None:
    monkeypatch.setattr(trust_gateway.settings, "TRUST_GATEWAY_KEY", key)
    monkeypatch.setattr(trust_gateway.settings, "TRUST_GATEWAY_ALGORITHMS", algorithms)
    monkeypatch.setattr(trust_gateway.settings, "TRUST_GATEWAY_ISSUER", issuer)
    monkeypatch.setattr(trust_gateway.settings, "TRUST_GATEWAY_AUDIENCE", audience)


def _payload(
    agent_id: str = "agent-1",
    *,
    kya: object = 1,
    reputation: object = 0.87,
    effective: object | None = None,
    include_effective: bool = True,
    org_id: object = "",
    subject: str | None = None,
    issuer: str = ISSUER,
    audience: object = AUDIENCE,
    exp: int | None = None,
    omit_exp: bool = False,
) -> dict:
    trust: dict = {
        "reputation": reputation,
        "kya_level": kya,
        "delegation_depth": 0,
        "decay_factor": 0.85,
    }
    if include_effective:
        trust["effective_trust"] = reputation if effective is None else effective
    claims = {
        "agent_id": agent_id,
        "org_id": org_id,
        "spending_limits": {},
        "counterparty_policy": {},
        "delegation": {"chain": [], "transferable": False},
        "trust": trust,
    }
    payload: dict = {
        "sub": f"agent:{agent_id}" if subject is None else subject,
        "iss": issuer,
        "aud": audience,
        "iat": int(time.time()),
        "jti": "jti-1",
        "scope": "mcp:read",
        CLAIMS_NAMESPACE: claims,
    }
    if not omit_exp:
        payload["exp"] = int(time.time()) + 600 if exp is None else exp
    return payload


def _sign(payload: dict, key: str, algorithm: str) -> str:
    encoded = jwt.encode(payload, key, algorithm=algorithm)
    if isinstance(encoded, bytes):
        return encoded.decode("ascii")
    return encoded


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _none_token(payload: dict) -> str:
    header = _b64(json.dumps({"alg": "none", "typ": "JWT"}, separators=(",", ":")).encode())
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    return f"{header}.{body}."


def test_verify_rs256(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=_PUBLIC_PEM, algorithms="RS256")
    claims = verify_gateway_token(_sign(_payload(org_id="org-7"), _PRIVATE_PEM, "RS256"))
    assert claims.agent_id == "agent-1"
    assert claims.org_id == "org-7"
    assert claims.kya_level == 1
    assert claims.reputation == 0.87
    assert claims.raw["agent_id"] == "agent-1"


def test_verify_hs256(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    claims = verify_gateway_token(_sign(_payload(), HMAC_SECRET, "HS256"))
    assert claims.agent_id == "agent-1"
    assert claims.kya_level == 1
    assert claims.reputation == 0.87


def test_pem_literal_newlines(monkeypatch: pytest.MonkeyPatch):
    escaped = _PUBLIC_PEM.replace("\n", "\\n")
    assert "\n" not in escaped
    _configure(monkeypatch, key=escaped, algorithms="RS256")
    claims = verify_gateway_token(_sign(_payload(), _PRIVATE_PEM, "RS256"))
    assert claims.agent_id == "agent-1"


def test_wrong_key(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key="other-secret", algorithms="HS256")
    token = _sign(_payload(), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_wrong_rsa_key(monkeypatch: pytest.MonkeyPatch):
    _other_private, other_public = _rsa_pair()
    _configure(monkeypatch, key=other_public, algorithms="RS256")
    token = _sign(_payload(), _PRIVATE_PEM, "RS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_expired_token(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(exp=int(time.time()) - 30), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_missing_exp(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(omit_exp=True), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_wrong_audience(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(audience="https://evil.example"), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_audience_list_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(audience=[AUDIENCE]), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_wrong_issuer(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(issuer="https://evil.example"), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_empty_issuer_config_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256", issuer="")
    token = _sign(_payload(issuer=""), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_blank_issuer_config_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256", issuer="   ")
    token = _sign(_payload(issuer="   "), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_empty_audience_config_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256", audience="")
    token = _sign(_payload(audience=""), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_empty_key_rejects_token_signed_with_empty_secret(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key="", algorithms="HS256")
    token = _sign(_payload(), "", "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_whitespace_key_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key="   ", algorithms="HS256")
    token = _sign(_payload(), "   ", "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_none_algorithm_token_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=_PUBLIC_PEM, algorithms="RS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(_none_token(_payload()))


def test_none_in_allowlist_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="none")
    token = _sign(_payload(), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_hs_token_rejected_when_only_rs256_allowed(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=_PUBLIC_PEM, algorithms="RS256")
    token = _sign(_payload(), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_mixed_hmac_and_asymmetric_allowlist_is_rejected(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=_PUBLIC_PEM, algorithms="RS256, HS256")
    token = _sign(_payload(), _PRIVATE_PEM, "RS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


@pytest.mark.parametrize(
    ("subject", "agent_id"),
    [
        ("agent:other", "agent-1"),
        ("agent-1", "agent-1"),
        ("agent:agent-1 ", "agent-1"),
        ("Agent:agent-1", "agent-1"),
    ],
)
def test_subject_must_match_agent_id(monkeypatch: pytest.MonkeyPatch, subject: str, agent_id: str):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(agent_id, subject=subject), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


@pytest.mark.parametrize("kya", [6, -1, True, False, 1.0, "1"])
def test_kya_out_of_range_or_not_int(monkeypatch: pytest.MonkeyPatch, kya: object):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(kya=kya), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


@pytest.mark.parametrize("kya,reputation", [(0, 0.0), (5, 1.0), (3, 0), (5, 1)])
def test_kya_and_reputation_boundaries(
    monkeypatch: pytest.MonkeyPatch, kya: int, reputation: float
):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(kya=kya, reputation=reputation), HMAC_SECRET, "HS256")
    claims = verify_gateway_token(token)
    assert claims.kya_level == kya
    assert claims.reputation == float(reputation)


def test_effective_trust_is_preferred(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(reputation=0.95, effective=0.42), HMAC_SECRET, "HS256")
    assert verify_gateway_token(token).reputation == 0.42


def test_effective_trust_zero_is_not_treated_as_missing(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(reputation=1.0, effective=0.0), HMAC_SECRET, "HS256")
    assert verify_gateway_token(token).reputation == 0.0


def test_reputation_used_when_effective_trust_absent(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(reputation=0.66, include_effective=False), HMAC_SECRET, "HS256")
    assert verify_gateway_token(token).reputation == 0.66


@pytest.mark.parametrize("reputation", [1.01, -0.01, True, False, "0.5", None])
def test_reputation_out_of_range(monkeypatch: pytest.MonkeyPatch, reputation: object):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(
        _payload(reputation=reputation, include_effective=False),
        HMAC_SECRET,
        "HS256",
    )
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_invalid_effective_trust_does_not_fall_back(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    token = _sign(_payload(reputation=0.5, effective=1.4), HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_missing_claims_namespace(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    payload = _payload()
    del payload[CLAIMS_NAMESPACE]
    token = _sign(payload, HMAC_SECRET, "HS256")
    with pytest.raises(TrustTokenError):
        verify_gateway_token(token)


def test_trust_denial_passes_when_requirements_are_met(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    claims = verify_gateway_token(_sign(_payload(), HMAC_SECRET, "HS256"))
    assert trust_denial(claims, required_kya=1, required_ema=0.87) is None
    assert trust_denial(claims, required_kya=0, required_ema=0.5) is None


def test_trust_denial_shape_when_one_requirement_fails(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    claims = verify_gateway_token(_sign(_payload(kya=1, reputation=0.9), HMAC_SECRET, "HS256"))
    denial = trust_denial(claims, required_kya=2, required_ema=0.5)
    assert denial == {
        "error": "trust_insufficient",
        "error_type": "trust_insufficient",
        "tool": "claim_github_bounty_lease",
        "evaluations": {
            "kya_level": {"passed": False, "required": 2, "current": 1},
            "reputation": {"passed": True, "required": 0.5, "current": 0.9},
        },
        "upgrade_path": "Raise KYA level or reputation on the A2A Settlement Exchange",
    }


def test_trust_denial_when_both_requirements_fail(monkeypatch: pytest.MonkeyPatch):
    _configure(monkeypatch, key=HMAC_SECRET, algorithms="HS256")
    claims = verify_gateway_token(_sign(_payload(kya=0, reputation=0.2), HMAC_SECRET, "HS256"))
    denial = trust_denial(claims, required_kya=1, required_ema=0.75)
    assert denial is not None
    assert denial["evaluations"]["kya_level"]["passed"] is False
    assert denial["evaluations"]["reputation"] == {
        "passed": False,
        "required": 0.75,
        "current": 0.2,
    }


def test_bearer_token_parses_credential():
    assert bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"


@pytest.mark.parametrize(
    "value",
    [None, "", "Bearer", "Bearer ", "bearer abc", "Basic abc", "Bearer abc def", "Bearer abc\n"],
)
def test_bearer_token_rejects_absent_or_malformed(value: str | None):
    with pytest.raises(TrustTokenError):
        bearer_token(value)
