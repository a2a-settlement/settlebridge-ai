"""Agent health monitor with latency tracking, error rates, and periodic pings."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
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

    @property
    def avg_latency_ms(self) -> float | None:
        if not self.latencies:
            return None
        return sum(self.latencies) / len(self.latencies)

    @property
    def error_rate(self) -> float:
        if self.request_count == 0:
            return 0.0
        return self.error_count / self.request_count

    @property
    def status(self) -> str:
        if not self.last_seen:
            return "offline"
        age = (datetime.now(timezone.utc) - self.last_seen).total_seconds()
        if age > settings.HEALTH_CHECK_INTERVAL_S * 3:
            return "offline"
        if not self.last_ping_ok or self.error_rate > 0.5:
            return "degraded"
        return "active"


class HealthMonitor:
    """Tracks per-agent health: latency, error rate, last seen, periodic ping."""

    def __init__(self, exchange_health_url: str | None = None) -> None:
        self._agents: dict[str, AgentStats] = {}
        self._running = False
        self._exchange_health_url = exchange_health_url

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

            if no_url_agents and self._exchange_health_url:
                await self._heartbeat_via_exchange(client, no_url_agents)

    async def _heartbeat_via_exchange(
        self, client: httpx.AsyncClient, agents: list[AgentStats]
    ) -> None:
        """Single exchange health check; if reachable, mark all exchange-registered agents alive."""
        try:
            resp = await client.get(self._exchange_health_url)
            if resp.status_code < 400:
                now = datetime.now(timezone.utc)
                for stats in agents:
                    stats.last_seen = now
                    stats.last_ping_ok = True
        except Exception:
            logger.debug("Exchange heartbeat failed at %s", self._exchange_health_url)

    async def _ping_one(self, client: httpx.AsyncClient, stats: AgentStats) -> None:
        try:
            start = time.monotonic()
            resp = await client.get(stats.ping_url)
            latency = (time.monotonic() - start) * 1000
            stats.last_seen = datetime.now(timezone.utc)
            stats.last_ping_ok = resp.status_code < 500
            stats.latencies.append(latency)
            if len(stats.latencies) > SLIDING_WINDOW_SIZE:
                stats.latencies = stats.latencies[-SLIDING_WINDOW_SIZE:]
        except Exception:
            stats.last_ping_ok = False
            logger.debug("Ping failed for %s at %s", stats.agent_id, stats.ping_url)

    def stop(self) -> None:
        self._running = False
