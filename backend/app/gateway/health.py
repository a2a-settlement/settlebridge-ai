"""Agent health monitor with latency tracking, error rates, and periodic pings."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

SLIDING_WINDOW_SIZE = 100


@dataclass
class AgentStats:
    agent_id: str
    bot_id: str = ""
    last_seen: datetime | None = None
    latencies: list[float] = field(default_factory=list)
    error_count: int = 0
    request_count: int = 0
    last_ping_ok: bool = True
    ping_url: str | None = None
    # Last exchange account status from per-agent probe ("active", "suspended", ...)
    exchange_status: str | None = None

    @property
    def avg_latency_ms(self) -> float | None:
        if not self.latencies:
            return None
        return sum(self.latencies) / len(self.latencies)

    @property
    def error_rate(self) -> float | None:
        # No samples yet — UI should show "—", not a fake 0.0%.
        if self.request_count == 0:
            return None
        return self.error_count / self.request_count

    @property
    def status(self) -> str:
        if not self.last_seen:
            return "offline"
        age = (datetime.now(timezone.utc) - self.last_seen).total_seconds()
        if age > settings.HEALTH_CHECK_INTERVAL_S * 3:
            return "offline"
        if self.exchange_status and self.exchange_status != "active":
            return "degraded"
        if not self.last_ping_ok or (self.error_rate is not None and self.error_rate > 0.5):
            return "degraded"
        return "active"


class HealthMonitor:
    """Tracks per-agent health: latency, error rate, last seen, periodic ping.

    Agents with ``ping_url`` are HTTP-probed directly.
    Otherwise each agent is probed individually via the exchange account API
    (never a single shared /health stamp that marks every bot "alive").
    """

    def __init__(
        self,
        exchange_health_url: str | None = None,
        exchange_base_url: str | None = None,
        exchange_api_key: str | None = None,
    ) -> None:
        self._agents: dict[str, AgentStats] = {}
        self._running = False
        self._exchange_health_url = exchange_health_url
        self._exchange_base_url = (exchange_base_url or "").rstrip("/") or None
        self._exchange_api_key = exchange_api_key

    def register_agent(
        self, agent_id: str, bot_id: str = "", ping_url: str | None = None
    ) -> None:
        if agent_id not in self._agents:
            self._agents[agent_id] = AgentStats(
                agent_id=agent_id, bot_id=bot_id, ping_url=ping_url
            )
        else:
            stats = self._agents[agent_id]
            if bot_id:
                stats.bot_id = bot_id
            if ping_url:
                stats.ping_url = ping_url

    def mark_alive(self, agent_id: str) -> None:
        """Mark an agent as alive (e.g. confirmed active on the exchange)."""
        stats = self._agents.get(agent_id)
        if stats:
            stats.last_seen = datetime.now(timezone.utc)
            stats.last_ping_ok = True
            if stats.exchange_status is None:
                stats.exchange_status = "active"

    def record_request(
        self, agent_id: str, latency_ms: float, is_error: bool = False
    ) -> None:
        stats = self._agents.get(agent_id)
        if not stats:
            stats = AgentStats(agent_id=agent_id)
            self._agents[agent_id] = stats

        stats.last_seen = datetime.now(timezone.utc)
        stats.request_count += 1
        if is_error:
            stats.error_count += 1

        stats.latencies.append(latency_ms)
        if len(stats.latencies) > SLIDING_WINDOW_SIZE:
            stats.latencies = stats.latencies[-SLIDING_WINDOW_SIZE:]

    def get_agent(self, agent_id: str) -> AgentStats | None:
        return self._agents.get(agent_id)

    def get_all_agents(self) -> list[AgentStats]:
        return list(self._agents.values())

    def unregister_agent(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)

    async def start_ping_loop(self) -> None:
        self._running = True
        # Probe once immediately so the UI is not empty until the first interval.
        await self._ping_all()
        while self._running:
            await asyncio.sleep(settings.HEALTH_CHECK_INTERVAL_S)
            await self._ping_all()

    async def _ping_all(self) -> None:
        async with httpx.AsyncClient(timeout=5.0) as client:
            tasks = []
            no_url_agents: list[AgentStats] = []
            for stats in self._agents.values():
                if stats.ping_url:
                    tasks.append(self._ping_one(client, stats))
                else:
                    no_url_agents.append(stats)
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            if no_url_agents:
                if self._exchange_base_url:
                    await asyncio.gather(
                        *[
                            self._probe_exchange_account(client, stats)
                            for stats in no_url_agents
                        ],
                        return_exceptions=True,
                    )
                elif self._exchange_health_url:
                    # Legacy fallback: only prove the exchange process is up —
                    # do NOT stamp every agent with the same last_seen.
                    logger.debug(
                        "No exchange_base_url; skipping per-agent probes (%d agents)",
                        len(no_url_agents),
                    )

    async def _probe_exchange_account(
        self, client: httpx.AsyncClient, stats: AgentStats
    ) -> None:
        """Per-agent liveness: GET /v1/accounts/{id} and record that probe's latency."""
        url = f"{self._exchange_base_url}/v1/accounts/{stats.agent_id}"
        headers: dict[str, str] = {}
        if self._exchange_api_key:
            headers["Authorization"] = f"Bearer {self._exchange_api_key}"

        try:
            start = time.monotonic()
            resp = await client.get(url, headers=headers)
            latency = (time.monotonic() - start) * 1000

            if resp.status_code == 404:
                stats.last_ping_ok = False
                stats.exchange_status = "missing"
                stats.request_count += 1
                stats.error_count += 1
                return

            if resp.status_code >= 500:
                stats.last_ping_ok = False
                self.record_request(stats.agent_id, latency, is_error=True)
                return

            body: dict = {}
            try:
                body = resp.json() if resp.content else {}
            except Exception:
                body = {}

            acct = body.get("account") if isinstance(body.get("account"), dict) else body
            status = (acct.get("status") or body.get("status") or "unknown").lower()
            stats.exchange_status = status

            # Probe succeeded (we reached the exchange and got an account payload).
            # Suspended/inactive is reflected in status="degraded", not error_rate —
            # error_rate is for failed probes / gateway proxy failures.
            reachable = resp.status_code < 400
            stats.last_ping_ok = reachable and status in ("active", "ok", "")
            if reachable and status in ("", "unknown"):
                stats.last_ping_ok = True

            self.record_request(stats.agent_id, latency, is_error=not reachable)
            if reachable and acct.get("bot_name") and not stats.bot_id:
                stats.bot_id = acct["bot_name"]
        except Exception:
            stats.last_ping_ok = False
            logger.debug("Exchange account probe failed for %s", stats.agent_id)

    async def _ping_one(self, client: httpx.AsyncClient, stats: AgentStats) -> None:
        try:
            start = time.monotonic()
            resp = await client.get(stats.ping_url)
            latency = (time.monotonic() - start) * 1000
            ok = resp.status_code < 500
            stats.last_ping_ok = ok
            self.record_request(stats.agent_id, latency, is_error=not ok)
        except Exception:
            stats.last_ping_ok = False
            logger.debug("Ping failed for %s at %s", stats.agent_id, stats.ping_url)

    def stop(self) -> None:
        self._running = False
