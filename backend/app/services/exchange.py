"""Thin wrapper around the a2a-settlement SDK."""

from __future__ import annotations

import logging
from typing import Any

from a2a_settlement.client import SettlementExchangeClient

from app.config import settings
from app.models.user import User

logger = logging.getLogger(__name__)


def _public_client() -> SettlementExchangeClient:
    return SettlementExchangeClient(base_url=settings.A2A_EXCHANGE_URL)


def _user_client(user: User) -> SettlementExchangeClient:
    if not user.exchange_api_key:
        raise ValueError("User has not linked an exchange account")
    return SettlementExchangeClient(
        base_url=settings.A2A_EXCHANGE_URL, api_key=user.exchange_api_key
    )


def register_account(bot_name: str, developer_id: str = "") -> dict[str, Any]:
    client = _public_client()
    return client.register_account(bot_name=bot_name, developer_id=developer_id)


def get_balance(user: User) -> dict[str, Any]:
    client = _user_client(user)
    return client.get_balance()


def create_escrow(user: User, provider_id: str, amount: int, task_id: str = "") -> dict[str, Any]:
    client = _user_client(user)
    return client.create_escrow(provider_id=provider_id, amount=amount, task_id=task_id)


def deliver(
    user: User,
    escrow_id: str,
    content: str,
    content_hash: str,
    provenance: dict | None = None,
) -> dict[str, Any]:
    client = _user_client(user)
    return client.deliver(
        escrow_id=escrow_id,
        content=content,
        content_hash=content_hash,
        provenance=provenance or {},
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
