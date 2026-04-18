"""Background service that syncs principal and diversity data from the exchange.

Polls GET /v1/accounts/{id}/counterparty-diversity for all known GatewayAgent
records and persists results into ReputationSnapshot alongside the EMA score.
Also updates the principal_id on GatewayAgent records via the principal endpoint.

Runs on the configurable PRINCIPAL_SYNC_INTERVAL_SECONDS cadence (default: 1h).
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import httpx
from sqlalchemy import select

from app.config import settings
from app.database import async_session
from app.models.gateway import GatewayAgent, ReputationSnapshot

logger = logging.getLogger(__name__)

PRINCIPAL_SYNC_INTERVAL_SECONDS = int(os.getenv("PRINCIPAL_SYNC_INTERVAL_SECONDS", "3600"))


async def _fetch_diversity(client: httpx.AsyncClient, agent_id: str) -> dict | None:
    """Fetch counterparty diversity metrics from the exchange for one agent."""
    try:
        resp = await client.get(
            f"{settings.effective_exchange_url}/v1/accounts/{agent_id}/counterparty-diversity",
            timeout=10.0,
        )
        if resp.status_code == 200:
            return resp.json()
        logger.debug("Diversity fetch for %s returned %d", agent_id, resp.status_code)
    except Exception as exc:
        logger.debug("Diversity fetch failed for %s: %s", agent_id, exc)
    return None


async def _fetch_principal_id(client: httpx.AsyncClient, agent_id: str) -> str | None:
    """Fetch the primary principal_id for an agent from the exchange."""
    try:
        resp = await client.get(
            f"{settings.effective_exchange_url}/v1/accounts/{agent_id}/principal",
            timeout=10.0,
        )
        if resp.status_code == 200:
            data = resp.json()
            links = data.get("links", [])
            if links:
                return links[0]["principal_id"]
    except Exception as exc:
        logger.debug("Principal fetch failed for %s: %s", agent_id, exc)
    return None


async def run_principal_sync() -> dict:
    """Sync diversity metrics and principal IDs for all claimed gateway agents.

    Returns a summary dict with counts of agents processed and updated.
    """
    gateway_api_key = settings.GATEWAY_EXCHANGE_API_KEY or settings.GATEWAY_EXCHANGE_URL
    headers: dict[str, str] = {}
    if gateway_api_key:
        headers["Authorization"] = f"Bearer {gateway_api_key}"

    processed = 0
    updated = 0

    async with httpx.AsyncClient(headers=headers) as http:
        async with async_session() as session:
            agents = (await session.execute(
                select(GatewayAgent).where(GatewayAgent.status == "active")
            )).scalars().all()

        for agent in agents:
            agent_id = agent.exchange_account_id
            processed += 1

            diversity = await _fetch_diversity(http, agent_id)
            principal_id_str = await _fetch_principal_id(http, agent_id)

            if diversity is None and principal_id_str is None:
                continue

            async with async_session() as session:
                async with session.begin():
                    # Update principal_id on the GatewayAgent record.
                    if principal_id_str is not None:
                        import uuid
                        try:
                            pid_uuid = uuid.UUID(principal_id_str)
                            agent_row = await session.get(GatewayAgent, agent.id)
                            if agent_row and agent_row.principal_id != pid_uuid:
                                agent_row.principal_id = pid_uuid
                                session.add(agent_row)
                        except (ValueError, TypeError):
                            pass

                    # Write a new ReputationSnapshot with diversity fields.
                    if diversity is not None:
                        snap = ReputationSnapshot(
                            agent_id=agent_id,
                            bot_id=agent_id,
                            reputation_score=0.0,  # updated by reputation_cache separately
                            counterparty_hhi=diversity.get("counterparty_hhi"),
                            snapshot_at=datetime.now(timezone.utc),
                        )
                        session.add(snap)
                        updated += 1

    logger.info(
        "Principal sync complete: %d agents processed, %d diversity records written",
        processed, updated,
    )
    return {"processed": processed, "updated": updated}


async def principal_sync_loop() -> None:
    """Periodic loop: sync principal and diversity data from the exchange."""
    interval = PRINCIPAL_SYNC_INTERVAL_SECONDS
    logger.info("Principal sync loop started (interval=%ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            result = await run_principal_sync()
            logger.info("Principal sync: %s", result)
        except Exception:
            logger.exception("Error in principal sync loop")
