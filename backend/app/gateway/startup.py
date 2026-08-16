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
        self._account_id: str | None = None
        self._account_type: str | None = None
        self._bot_name: str | None = None

    @property
    def exchange_connected(self) -> bool:
        return self._exchange_connected

    @property
    def exchange_client(self) -> SettlementExchangeClient | None:
        return self._exchange_client

    @property
    def gateway_account_id(self) -> str | None:
        return self._account_id

    @property
    def gateway_account_type(self) -> str | None:
        return self._account_type

    @property
    def can_claim_on_exchange(self) -> bool:
        """The exchange only accepts claims from accounts of type "gateway"."""
        return self._account_type == "gateway"

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
                    api_key = settings.GATEWAY_EXCHANGE_API_KEY or None
                    self._exchange_client = SettlementExchangeClient(
                        base_url=url, api_key=api_key,
                    )
                    logger.info("Exchange connected at %s (auth=%s)", url, bool(api_key))
                    if api_key:
                        await self._resolve_identity(client, url, api_key)
                    return True
                else:
                    logger.warning("Exchange health check returned %d", resp.status_code)
        except Exception:
            logger.warning("Exchange unreachable at %s; will retry", url)

        self._exchange_connected = False
        return False

    async def _resolve_identity(
        self, client: httpx.AsyncClient, url: str, api_key: str
    ) -> None:
        """Resolve which exchange account this gateway authenticates as.

        Claiming agents on the exchange requires an account of type "gateway".
        An agent-type key silently produces unverified, unrecorded claims, so
        surface the mismatch loudly at startup.
        """
        base = url.rstrip("/")
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            resp = await client.get(f"{base}/v1/exchange/balance", headers=headers)
            if resp.status_code >= 400:
                logger.warning(
                    "Could not identify gateway exchange account (%d)", resp.status_code
                )
                return
            self._account_id = (resp.json() or {}).get("account_id")
            if not self._account_id:
                return

            resp = await client.get(
                f"{base}/v1/accounts/{self._account_id}", headers=headers
            )
            if resp.status_code >= 400:
                return
            acct = resp.json() or {}
            self._account_type = acct.get("account_type", "agent")
            self._bot_name = acct.get("bot_name")
        except Exception:
            logger.warning("Gateway identity probe failed", exc_info=True)
            return

        if self.can_claim_on_exchange:
            logger.info(
                "Gateway authenticates as %s (%s)", self._bot_name, self._account_id
            )
        else:
            logger.warning(
                "GATEWAY_EXCHANGE_API_KEY belongs to '%s' (account_type=%s), not a "
                "gateway account. Exchange claims will be rejected and agents will "
                "stay unverified. Register an account_type=gateway account.",
                self._bot_name,
                self._account_type,
            )

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
