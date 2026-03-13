"""Gateway startup auto-connect: probe exchange, register gateway, health check loop."""

from __future__ import annotations

import asyncio
import logging

import httpx
from a2a_settlement.client import SettlementExchangeClient

from app.config import settings

logger = logging.getLogger(__name__)


class GatewayStartup:
    """Manages startup connection to the settlement exchange and ongoing health checks."""

    def __init__(self) -> None:
        self._exchange_client: SettlementExchangeClient | None = None
        self._exchange_connected = False
        self._running = False

    @property
    def exchange_connected(self) -> bool:
        return self._exchange_connected

    @property
    def exchange_client(self) -> SettlementExchangeClient | None:
        return self._exchange_client

    async def probe_and_connect(self) -> bool:
        url = settings.effective_exchange_url
        if not url:
            logger.warning("No exchange URL configured; gateway operates in standalone mode")
            return False

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url.rstrip('/')}/health")
                if resp.status_code < 400:
                    self._exchange_connected = True
                    self._exchange_client = SettlementExchangeClient(base_url=url)
                    logger.info("Exchange connected at %s", url)
                    return True
                else:
                    logger.warning("Exchange health check returned %d", resp.status_code)
        except Exception:
            logger.warning("Exchange unreachable at %s; will retry", url)

        self._exchange_connected = False
        return False

    async def start_health_loop(self) -> None:
        self._running = True
        while self._running:
            await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL_S)
            if not self._exchange_connected:
                await self.probe_and_connect()
            else:
                await self._check_exchange()

    async def _check_exchange(self) -> None:
        url = settings.effective_exchange_url
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{url.rstrip('/')}/health")
                self._exchange_connected = resp.status_code < 400
        except Exception:
            self._exchange_connected = False
            logger.warning("Exchange connection lost; serving cached data")

    def stop(self) -> None:
        self._running = False
