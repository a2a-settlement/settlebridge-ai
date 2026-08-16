"""Thin wrapper around the a2a-settlement SDK."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from a2a_settlement.client import SettlementExchangeClient

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

_TIER_MAP = {
    "tier1_self_declared": "self_declared",
    "tier2_signed": "signed",
    "tier3_verifiable": "verifiable",
}


def _map_provenance(prov: dict, content_hash: str | None = None) -> dict:
    """Map SettleBridge provenance format to exchange Provenance schema."""
    source_refs_raw = prov.get("source_refs") or []
    timestamps_raw = prov.get("timestamps") or []

    ts_by_url: dict[str, str] = {}
    for t in timestamps_raw:
        url = t.get("url", "")
        ts_by_url[url] = t.get("accessed", datetime.now(timezone.utc).isoformat())

    source_refs = []
    for ref in source_refs_raw:
        uri = ref if isinstance(ref, str) else ref.get("uri", "")
        source_refs.append({
            "uri": uri,
            "timestamp": ts_by_url.get(uri, datetime.now(timezone.utc).isoformat()),
            "content_hash": content_hash,
        })

    return {
        "source_type": prov.get("source_type", "generated"),
        "attestation_level": prov.get("attestation_level", "self_declared"),
        "source_refs": source_refs,
        "signature": prov.get("signature"),
    }


def _public_client() -> SettlementExchangeClient:
    return SettlementExchangeClient(base_url=settings.effective_exchange_url)


def _user_client(user: User) -> SettlementExchangeClient:
    if not user.exchange_api_key:
        raise ValueError("User has not linked an exchange account")
    return SettlementExchangeClient(
        base_url=settings.effective_exchange_url, api_key=user.exchange_api_key
    )


def register_account(
    bot_name: str,
    developer_id: str,
    developer_name: str,
    contact_email: str,
    description: str | None = None,
    skills: list[str] | None = None,
) -> dict[str, Any]:
    client = _public_client()
    return client.register_account(
        bot_name=bot_name,
        developer_id=developer_id,
        developer_name=developer_name,
        contact_email=contact_email,
        description=description,
        skills=skills,
    )


def get_balance(user: User) -> dict[str, Any]:
    client = _user_client(user)
    return client.get_balance()


def get_balance_dashboard(account_id: str) -> dict[str, Any]:
    """Check balance for a specific account using the operator dashboard API key.

    Immune to user-key rotation — uses the stable server-side dashboard key.
    Returns a dict with at least {"available": int, "account_id": str}.
    """
    import httpx

    if not settings.A2A_DASHBOARD_API_KEY:
        raise ValueError("A2A_DASHBOARD_API_KEY not configured on this server")

    base = settings.effective_exchange_url.rstrip("/")
    resp = httpx.get(
        f"{base}/v1/agents/{account_id}",
        headers={"Authorization": f"Bearer {settings.A2A_DASHBOARD_API_KEY}"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "available": int(data.get("balance", 0)),
        "account_id": account_id,
        "status": data.get("status"),
        "reputation": data.get("reputation"),
    }


def create_escrow(
    user: User,
    provider_id: str,
    amount: int,
    task_id: str = "",
    required_attestation_level: str | None = None,
    ttl_minutes: int | None = None,
) -> dict[str, Any]:
    client = _user_client(user)
    att_level = _TIER_MAP.get(
        (required_attestation_level or "").lower(), required_attestation_level
    )
    return client.create_escrow(
        provider_id=provider_id,
        amount=amount,
        task_id=task_id,
        required_attestation_level=att_level,
        ttl_minutes=ttl_minutes,
    )


def deliver(
    user: User,
    escrow_id: str,
    content: str,
    content_hash: str | None = None,
    provenance: dict | None = None,
) -> dict[str, Any]:
    mapped = _map_provenance(provenance, content_hash) if provenance else None
    client = _user_client(user)
    return client.deliver(
        escrow_id=escrow_id,
        content=content,
        provenance=mapped,
    )


def partial_release(
    user: User,
    escrow_id: str,
    release_percent: int,
    score: int | None = None,
    efficacy_check_at: str | None = None,
    efficacy_criteria: str | None = None,
) -> dict[str, Any]:
    client = _user_client(user)
    return client.partial_release(
        escrow_id=escrow_id,
        release_percent=release_percent,
        score=score,
        efficacy_check_at=efficacy_check_at,
        efficacy_criteria=efficacy_criteria,
    )


def release_escrow(user: User, escrow_id: str) -> dict[str, Any]:
    client = _user_client(user)
    return client.release_escrow(escrow_id=escrow_id)


def refund_escrow(user: User, escrow_id: str, reason: str = "") -> dict[str, Any]:
    client = _user_client(user)
    return client.refund_escrow(escrow_id=escrow_id, reason=reason)


def dispute_escrow(user: User, escrow_id: str, reason: str = "") -> dict[str, Any]:
    client = _user_client(user)
    return client.dispute_escrow(escrow_id=escrow_id, reason=reason)


def get_escrow(user: User, escrow_id: str) -> dict[str, Any]:
    import httpx
    url = f"{settings.effective_exchange_url}/v1/exchange/escrows/{escrow_id}"
    resp = httpx.get(url, headers={"Authorization": f"Bearer {user.exchange_api_key}"}, timeout=10)
    resp.raise_for_status()
    return resp.json()


def get_escrow_status(escrow_id: str, api_key: str | None = None) -> str | None:
    """Fetch escrow status string. Uses api_key or the dashboard operator key."""
    import httpx

    key = api_key or settings.A2A_DASHBOARD_API_KEY
    if not key:
        return None
    url = f"{settings.effective_exchange_url}/v1/exchange/escrows/{escrow_id}"
    try:
        resp = httpx.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("status")
    except Exception as exc:
        logger.warning("Failed to fetch escrow %s status: %s", escrow_id, exc)
        return None


TERMINAL_ESCROW_STATUSES = frozenset({"expired", "refunded"})


def is_escrow_terminal(user: User, escrow_id: str) -> bool:
    """True when the exchange escrow is expired or refunded (not partially_released)."""
    try:
        data = get_escrow(user, escrow_id)
        return data.get("status") in TERMINAL_ESCROW_STATUSES
    except Exception as exc:
        logger.warning("Failed to check escrow status for %s: %s", escrow_id, exc)
        return False


def is_escrow_expired(user: User, escrow_id: str) -> bool:
    """Backward-compatible alias: expired or refunded count as terminal."""
    return is_escrow_terminal(user, escrow_id)


def recreate_and_release(
    requester: User,
    provider_bot_id: str,
    provider_api_key: str,
    amount: int,
    task_id: str,
    content: str,
    content_hash: str | None = None,
    provenance: dict | None = None,
    required_attestation_level: str | None = None,
) -> str:
    """Create a fresh escrow, deliver content, and release — returns new escrow_id."""
    import httpx
    import re

    try:
        escrow_result = create_escrow(
            requester,
            provider_id=provider_bot_id,
            amount=amount,
            task_id=task_id,
            required_attestation_level=required_attestation_level,
            ttl_minutes=settings.ESCROW_TTL_MINUTES,
        )
        new_escrow_id = escrow_result.get("escrow_id", escrow_result.get("id", ""))
        logger.info("Created replacement escrow %s for task %s", new_escrow_id, task_id)
    except Exception as exc:
        err_str = str(exc)
        if "409" in err_str:
            match = re.search(r"escrow_id=([a-f0-9-]+)", err_str)
            if match:
                new_escrow_id = match.group(1)
                logger.info("Reusing existing escrow %s for task %s", new_escrow_id, task_id)
            else:
                raise
        else:
            raise

    mapped = _map_provenance(provenance, content_hash) if provenance else None
    deliver_url = f"{settings.effective_exchange_url}/v1/exchange/escrow/{new_escrow_id}/deliver"
    deliver_payload: dict[str, Any] = {"content": content}
    if mapped:
        deliver_payload["provenance"] = mapped
    headers = {"Authorization": f"Bearer {provider_api_key}", "Content-Type": "application/json"}
    resp = httpx.post(deliver_url, json=deliver_payload, headers=headers, timeout=15)
    resp.raise_for_status()
    logger.info("Re-delivered content to escrow %s", new_escrow_id)

    release_escrow(requester, new_escrow_id)
    logger.info("Released replacement escrow %s", new_escrow_id)
    return new_escrow_id


def get_directory() -> list[dict[str, Any]]:
    client = _public_client()
    return client.directory()


def get_account(account_id: str) -> dict[str, Any]:
    client = _public_client()
    return client.get_account(account_id=account_id)


def get_agent_card(account_id: str) -> dict[str, Any] | None:
    """Fetch the stored Agent Card for an account, or None if not published."""
    import httpx

    url = f"{settings.effective_exchange_url}/v1/accounts/{account_id}/card"
    try:
        resp = httpx.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.warning("Failed to fetch agent card for %s: %s", account_id, exc)
        return None


def list_managed_bots(user: User, developer_id: str | None = None) -> dict[str, Any]:
    """Return bots managed by this user's exchange account, filtered by developer_id."""
    import httpx
    dev_id = developer_id or user.managed_developer_id or ""  # type: ignore[attr-defined]
    params: dict[str, str | int] = {"limit": 100}
    if dev_id:
        params["developer_id"] = dev_id
    resp = httpx.get(
        f"{settings.effective_exchange_url}/v1/accounts/managed-bots",
        params=params,
        headers={"Authorization": f"Bearer {user.exchange_api_key}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def rotate_bot_key(user: User, bot_id: str) -> dict[str, Any]:
    """Rotate the API key for a managed bot. Returns new ate_ key (once only)."""
    import httpx
    resp = httpx.post(
        f"{settings.effective_exchange_url}/v1/accounts/{bot_id}/rotate-key-managed",
        headers={"Authorization": f"Bearer {user.exchange_api_key}"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


def update_managed_developer_id(user: User, developer_id: str) -> None:
    """Update the developer_id namespace this user manages (in-memory only — caller must commit)."""
    user.managed_developer_id = developer_id  # type: ignore[attr-defined]
