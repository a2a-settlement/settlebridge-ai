"""Thin wrapper around the a2a-settlement SDK."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from a2a_settlement.client import SettlementExchangeClient

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)

TIER_MAP = {
    "TIER1_SELF_DECLARED": "self_declared",
    "TIER2_SIGNED": "signed",
    "TIER3_VERIFIABLE": "verifiable",
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
    return SettlementExchangeClient(base_url=settings.A2A_EXCHANGE_URL)


def _user_client(user: User) -> SettlementExchangeClient:
    if not user.exchange_api_key:
        raise ValueError("User has not linked an exchange account")
    return SettlementExchangeClient(
        base_url=settings.A2A_EXCHANGE_URL, api_key=user.exchange_api_key
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


def create_escrow(
    user: User,
    provider_id: str,
    amount: int,
    task_id: str = "",
    required_attestation_level: str | None = None,
) -> dict[str, Any]:
    client = _user_client(user)
    att_level = TIER_MAP.get(required_attestation_level, required_attestation_level)
    return client.create_escrow(
        provider_id=provider_id,
        amount=amount,
        task_id=task_id,
        required_attestation_level=att_level,
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


def get_directory() -> list[dict[str, Any]]:
    client = _public_client()
    return client.directory()


def get_account(account_id: str) -> dict[str, Any]:
    client = _public_client()
    return client.get_account(account_id=account_id)
