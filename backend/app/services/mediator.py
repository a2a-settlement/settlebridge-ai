"""HTTP client for the A2A Settlement Mediator service."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT = 30.0


async def trigger_mediation(escrow_id: str) -> dict[str, Any]:
    """Trigger mediation for an escrow (used for auto-approval and dispute resolution)."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        url = f"{settings.MEDIATOR_URL}/mediate/{escrow_id}"
        resp = await client.post(url)
        resp.raise_for_status()
        return resp.json()


async def get_audit(escrow_id: str) -> dict[str, Any] | None:
    """Fetch the mediation audit record for an escrow."""
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        url = f"{settings.MEDIATOR_URL}/audits/{escrow_id}"
        resp = await client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()
