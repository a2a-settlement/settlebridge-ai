"""Local verification of mcp-trust-gateway access tokens.

``AgentClaims.reputation`` is ``trust.effective_trust`` when that field is
present, because effective trust already includes delegation decay. When
``effective_trust`` is absent, ``trust.reputation`` is used. An invalid
``effective_trust`` is rejected and is not replaced by ``reputation``.
``raw`` is the verified claims-namespace object.
``scopes`` unions the top-level ``scope`` string with a ``scp`` list.
A missing or unusable scope claim is an empty set.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jose import JOSEError, jwt

from app.config import settings

CLAIMS_NAMESPACE = "https://a2a-settlement.org/claims"
_AGENT_PREFIX = "agent:"
_BEARER_PREFIX = "Bearer "
_HMAC_PREFIX = "HS"
_KYA_MIN = 0
_KYA_MAX = 5
FINANCIAL_SCOPE = "mcp:tool:financial"
READ_SCOPE = "mcp:read"


class TrustTokenError(Exception):
    """The token is missing, malformed, unsigned by the gateway, or expired."""


@dataclass(frozen=True)
class AgentClaims:
    agent_id: str
    org_id: str
    kya_level: int
    reputation: float
    scopes: frozenset[str] = field(default_factory=frozenset)
    raw: dict = field(default_factory=dict)


def verify_gateway_token(token: str) -> AgentClaims:
    if not isinstance(token, str) or token.strip() == "":
        raise TrustTokenError("missing trust gateway token")
    algorithms = _algorithms()
    key = _verification_key()
    issuer = _required_setting(
        settings.TRUST_GATEWAY_ISSUER, "trust gateway issuer is not configured"
    )
    audience = _required_setting(
        settings.TRUST_GATEWAY_AUDIENCE, "trust gateway audience is not configured"
    )
    _reject_disallowed_header(token, algorithms)
    try:
        payload = jwt.decode(
            token,
            key,
            algorithms=algorithms,
            audience=audience,
            issuer=issuer,
            options={
                "verify_signature": True,
                "verify_aud": True,
                "verify_iss": True,
                "verify_exp": True,
                "verify_nbf": True,
                "require_aud": True,
                "require_iss": True,
                "require_exp": True,
            },
        )
    except JOSEError as exc:
        raise TrustTokenError("invalid trust gateway token") from exc
    if not isinstance(payload, dict):
        raise TrustTokenError("invalid trust gateway token")
    # jose treats a list audience as a match. Payout tokens must name this audience exactly.
    if payload.get("aud") != audience or payload.get("iss") != issuer:
        raise TrustTokenError("invalid trust gateway token")
    return _agent_claims(payload)


def trust_denial(
    claims: AgentClaims, *, required_kya: int, required_ema: float
) -> dict | None:
    """Return a ``trust_insufficient`` body when requirements are unmet, else None."""
    kya_passed = claims.kya_level >= required_kya
    reputation_passed = claims.reputation >= required_ema
    if kya_passed and reputation_passed:
        return None
    return {
        "error": "trust_insufficient",
        "error_type": "trust_insufficient",
        "tool": "claim_github_bounty_lease",
        "evaluations": {
            "kya_level": {
                "passed": kya_passed,
                "required": required_kya,
                "current": claims.kya_level,
            },
            "reputation": {
                "passed": reputation_passed,
                "required": required_ema,
                "current": claims.reputation,
            },
        },
        "upgrade_path": "Raise KYA level or reputation on the A2A Settlement Exchange",
    }


def bearer_token(authorization: str | None) -> str:
    """Return the credential from an ``Authorization: Bearer <token>`` header."""
    if not isinstance(authorization, str) or not authorization.startswith(_BEARER_PREFIX):
        raise TrustTokenError("malformed bearer token")
    token = authorization[len(_BEARER_PREFIX) :]
    if token == "" or any(char.isspace() for char in token):
        raise TrustTokenError("malformed bearer token")
    return token


def _algorithms() -> list[str]:
    raw = settings.TRUST_GATEWAY_ALGORITHMS
    if not isinstance(raw, str) or raw.strip() == "":
        raise TrustTokenError("trust gateway algorithms are not configured")
    algorithms = [part.strip() for part in raw.split(",") if part.strip()]
    if not algorithms:
        raise TrustTokenError("trust gateway algorithms are not configured")
    if any(algorithm.lower() == "none" for algorithm in algorithms):
        raise TrustTokenError("trust gateway algorithm none is not allowed")
    # Mixing HMAC and asymmetric algorithms lets a token choose HS* and be
    # checked with the asymmetric public key as the HMAC secret.
    hmac = [algorithm for algorithm in algorithms if algorithm.upper().startswith(_HMAC_PREFIX)]
    if hmac and len(hmac) != len(algorithms):
        raise TrustTokenError("trust gateway algorithms must not mix HMAC and asymmetric")
    return algorithms


def _verification_key() -> str:
    raw = settings.TRUST_GATEWAY_KEY
    if not isinstance(raw, str):
        raise TrustTokenError("trust gateway key is not configured")
    # Env files store PEM line breaks as the two-character sequence \n.
    key = raw.replace("\\n", "\n").strip()
    if key == "":
        raise TrustTokenError("trust gateway key is not configured")
    return key


def _required_setting(value: object, message: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise TrustTokenError(message)
    return value.strip()


def _reject_disallowed_header(token: str, algorithms: list[str]) -> None:
    try:
        header = jwt.get_unverified_header(token)
    except JOSEError as exc:
        raise TrustTokenError("malformed trust gateway token") from exc
    algorithm = header.get("alg") if isinstance(header, dict) else None
    if not isinstance(algorithm, str) or algorithm.lower() == "none" or algorithm not in algorithms:
        raise TrustTokenError("trust gateway token algorithm is not allowed")


def _agent_claims(payload: dict) -> AgentClaims:
    claims = payload.get(CLAIMS_NAMESPACE)
    if not isinstance(claims, dict):
        raise TrustTokenError("trust gateway token is missing agent claims")
    agent_id = claims.get("agent_id")
    if not isinstance(agent_id, str) or agent_id == "" or agent_id != agent_id.strip():
        raise TrustTokenError("trust gateway token has an invalid agent_id")
    subject = payload.get("sub")
    if (
        not isinstance(subject, str)
        or not subject.startswith(_AGENT_PREFIX)
        or subject[len(_AGENT_PREFIX) :] != agent_id
    ):
        raise TrustTokenError("trust gateway token subject does not match agent_id")
    org_id = claims.get("org_id", "")
    if not isinstance(org_id, str):
        raise TrustTokenError("trust gateway token has an invalid org_id")
    trust = claims.get("trust")
    if not isinstance(trust, dict):
        raise TrustTokenError("trust gateway token is missing trust claims")
    kya_level = trust.get("kya_level")
    if type(kya_level) is not int or kya_level < _KYA_MIN or kya_level > _KYA_MAX:
        raise TrustTokenError("trust gateway token has an invalid kya_level")
    if "effective_trust" in trust:
        reputation_value = trust["effective_trust"]
    elif "reputation" in trust:
        reputation_value = trust["reputation"]
    else:
        raise TrustTokenError("trust gateway token is missing reputation")
    return AgentClaims(
        agent_id=agent_id,
        org_id=org_id,
        kya_level=kya_level,
        reputation=_unit_interval(reputation_value),
        scopes=_token_scopes(payload),
        raw=dict(claims),
    )


def _token_scopes(payload: dict) -> frozenset[str]:
    """Space-separated ``scope``, plus each string in a ``scp`` list."""
    found: set[str] = set()
    scope = payload.get("scope")
    if isinstance(scope, str):
        found.update(part for part in scope.split() if part)
    scp = payload.get("scp")
    if isinstance(scp, list):
        for item in scp:
            if isinstance(item, str) and item.strip():
                found.add(item.strip())
    return frozenset(found)


def _unit_interval(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TrustTokenError("trust gateway token has an invalid reputation")
    reputation = float(value)
    if reputation != reputation or reputation < 0.0 or reputation > 1.0:
        raise TrustTokenError("trust gateway token has an invalid reputation")
    return reputation
