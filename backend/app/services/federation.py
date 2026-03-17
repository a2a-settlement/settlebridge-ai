"""Federation service for federated reputation and trust policy discovery."""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


def _normalize_url(url: str) -> str:
    """Ensure exchange URL has no trailing slash."""
    return url.rstrip("/")


class FederationService:
    """Service for querying federated exchanges and trust policies."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def get_federated_reputation(
        self, agent_did: str, exchange_url: str
    ) -> dict[str, Any] | None:
        """Fetch agent's reputation from a federated exchange and apply trust discount.

        Fetches the agent's account (native reputation) from the federated exchange's
        account API, retrieves the peer's trust policy for rho, and computes effective
        reputation.

        Returns:
            Dict with native_reputation, rho, effective_reputation, exchange_url, or None on failure.
        """
        base = _normalize_url(exchange_url)
        policy = self.get_peer_trust_policy(exchange_url)
        if not policy:
            logger.debug("No trust policy for %s, skipping federated reputation", base)
            return None

        rho = policy.get("initial_rho")
        if rho is None:
            rho = policy.get("parameters", {}).get("initial_rho", 0.15)
        try:
            rho = float(rho)
        except (TypeError, ValueError):
            rho = 0.15

        try:
            url = f"{base}/v1/accounts/{agent_did}"
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(url)
                resp.raise_for_status()
        except Exception as exc:
            logger.debug("Failed to fetch federated account %s from %s: %s", agent_did, base, exc)
            return None

        data = resp.json()
        native_rep = data.get("reputation_score")
        if native_rep is None:
            return None

        try:
            native_rep = float(native_rep)
        except (TypeError, ValueError):
            return None

        effective = self.compute_effective_reputation(native_rep, rho)
        return {
            "native_reputation": native_rep,
            "rho": rho,
            "effective_reputation": effective,
            "exchange_url": base,
            "exchange_name": data.get("bot_name") or base,
        }

    def get_peer_trust_policy(self, exchange_url: str) -> dict[str, Any] | None:
        """Fetch a peer exchange's Trust Discount policy from /.well-known/a2a-trust-policy.json."""
        base = _normalize_url(exchange_url)
        url = f"{base}/.well-known/a2a-trust-policy.json"
        try:
            with httpx.Client(timeout=self._timeout) as client:
                resp = client.get(url)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.debug("Failed to fetch trust policy from %s: %s", url, exc)
            return None

    @staticmethod
    def compute_effective_reputation(native_reputation: float, rho: float) -> float:
        """Compute effective reputation as native_reputation * rho."""
        return native_reputation * rho
