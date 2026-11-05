"""Local verification of mcp-trust-gateway access tokens."""

from __future__ import annotations

from dataclasses import dataclass, field

CLAIMS_NAMESPACE = "https://a2a-settlement.org/claims"


class TrustTokenError(Exception):
    """The token is missing, malformed, unsigned by the gateway, or expired."""


@dataclass(frozen=True)
class AgentClaims:
    agent_id: str
    org_id: str
    kya_level: int
    reputation: float
    raw: dict = field(default_factory=dict)


def verify_gateway_token(token: str) -> AgentClaims:
    raise NotImplementedError


def trust_denial(
    claims: AgentClaims, *, required_kya: int, required_ema: float
) -> dict | None:
    """Return a ``trust_insufficient`` body when requirements are unmet, else None."""
    raise NotImplementedError
