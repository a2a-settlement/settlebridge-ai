"""Redis-backed reputation cache with exchange fallback and historical snapshots."""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.gateway import ReputationSnapshot

logger = logging.getLogger(__name__)

CACHE_PREFIX = "gw:rep:"
ATTESTATION_PREFIX = "gw:att:"


class ReputationCache:
    """Caches EMA reputation scores in Redis, falls back to exchange on miss."""

    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None
        self._exchange_client = None
        self._running = False
        self._hits = 0
        self._misses = 0

    async def connect(self) -> None:
        self._redis = aioredis.from_url(
            settings.REDIS_URL, decode_responses=True, socket_connect_timeout=5
        )
        try:
            await self._redis.ping()
            logger.info("Redis connected at %s", settings.REDIS_URL)
        except Exception:
            logger.warning("Redis unavailable, reputation cache operates without cache")
            self._redis = None

    def set_exchange_client(self, client) -> None:
        self._exchange_client = client

    async def close(self) -> None:
        if self._redis:
            await self._redis.aclose()
            self._redis = None

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    async def get(self, agent_id: str) -> float | None:
        if self._redis:
            try:
                val = await self._redis.get(f"{CACHE_PREFIX}{agent_id}")
                if val is not None:
                    self._hits += 1
                    return float(val)
            except Exception:
                logger.debug("Redis get failed for %s", agent_id)

        self._misses += 1
        score = await self._fetch_from_exchange(agent_id)
        if score is not None:
            await self._cache_set(agent_id, score)
        return score

    async def set(self, agent_id: str, score: float) -> None:
        await self._cache_set(agent_id, score)

    async def _cache_set(self, agent_id: str, score: float) -> None:
        if self._redis:
            try:
                await self._redis.set(
                    f"{CACHE_PREFIX}{agent_id}", str(score),
                    ex=settings.REPUTATION_CACHE_TTL_S,
                )
            except Exception:
                logger.debug("Redis set failed for %s", agent_id)

    async def _fetch_from_exchange(self, agent_id: str) -> float | None:
        if not self._exchange_client:
            return None
        try:
            account = self._exchange_client.get_account(account_id=agent_id)
            return account.get("reputation_score")
        except Exception:
            logger.debug("Exchange fetch failed for %s", agent_id)
            return None

    async def get_attestation_freshness(self, agent_id: str) -> dict | None:
        """Return cached attestation freshness or fetch from exchange."""
        if self._redis:
            try:
                val = await self._redis.get(f"{ATTESTATION_PREFIX}{agent_id}")
                if val is not None:
                    return json.loads(val)
            except Exception:
                logger.debug("Redis attestation get failed for %s", agent_id)

        freshness = await self._fetch_attestation_freshness(agent_id)
        if freshness is not None:
            await self._cache_attestation_freshness(agent_id, freshness)
        return freshness

    async def _fetch_attestation_freshness(self, agent_id: str) -> dict | None:
        if not self._exchange_client:
            return None
        try:
            resp = self._exchange_client.get(
                "/exchange/attestations",
                params={"account_id": agent_id, "status": "active"},
            )
            attestations = resp.json() if hasattr(resp, "json") else resp
            if isinstance(attestations, dict):
                attestations = attestations.get("attestations", [])

            now = datetime.now(timezone.utc)
            identity_att = next(
                (a for a in attestations if a.get("attestation_type") == "identity"),
                None,
            )
            capability_att = next(
                (a for a in attestations if a.get("attestation_type") == "capability"),
                None,
            )

            def _status(att: dict | None) -> str:
                if att is None:
                    return "unknown"
                return att.get("status", "unknown")

            identity_days = None
            if identity_att and identity_att.get("issued_at"):
                issued = datetime.fromisoformat(identity_att["issued_at"])
                identity_days = (now - issued).days

            return {
                "identity_verified_days_ago": identity_days,
                "identity_status": _status(identity_att),
                "capability_status": _status(capability_att),
                "attestation_valid": (
                    _status(identity_att) == "active"
                    and _status(capability_att) in ("active", "unknown")
                ),
            }
        except Exception:
            logger.debug("Exchange attestation fetch failed for %s", agent_id)
            return None

    async def _cache_attestation_freshness(
        self, agent_id: str, freshness: dict
    ) -> None:
        if self._redis:
            try:
                await self._redis.set(
                    f"{ATTESTATION_PREFIX}{agent_id}",
                    json.dumps(freshness),
                    ex=settings.REPUTATION_CACHE_TTL_S,
                )
            except Exception:
                logger.debug("Redis attestation set failed for %s", agent_id)

    async def snapshot(self, agent_id: str, bot_id: str, score: float) -> None:
        async with async_session() as session:
            snap = ReputationSnapshot(
                agent_id=agent_id,
                bot_id=bot_id,
                reputation_score=score,
                snapshot_at=datetime.now(timezone.utc),
            )
            session.add(snap)
            await session.commit()

    async def get_history(
        self, agent_id: str, session: AsyncSession, limit: int = 100
    ) -> list[ReputationSnapshot]:
        result = await session.execute(
            select(ReputationSnapshot)
            .where(ReputationSnapshot.agent_id == agent_id)
            .order_by(ReputationSnapshot.snapshot_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def start_refresh_loop(self) -> None:
        """Periodically refresh cached scores and store snapshots."""
        self._running = True
        while self._running:
            await asyncio.sleep(settings.REPUTATION_CACHE_TTL_S)
            await self._refresh_all()

    async def _refresh_all(self) -> None:
        if not self._redis:
            return
        try:
            cursor = "0"
            while cursor:
                cursor, keys = await self._redis.scan(
                    cursor=cursor, match=f"{CACHE_PREFIX}*", count=100
                )
                for key in keys:
                    agent_id = key.removeprefix(CACHE_PREFIX)
                    score = await self._fetch_from_exchange(agent_id)
                    if score is not None:
                        await self._cache_set(agent_id, score)
                        await self.snapshot(agent_id, agent_id, score)
                if cursor == "0":
                    break
        except Exception:
            logger.exception("Reputation refresh failed")

    def stop(self) -> None:
        self._running = False
