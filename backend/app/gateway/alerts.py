"""Alerts engine with configurable rules, threshold evaluation, and notification channels."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session
from app.models.gateway import (
    AlertChannel,
    AlertConditionType,
    AlertEvent,
    AlertRule,
    AuditEntry,
    PolicyDecisionType,
)

if TYPE_CHECKING:
    from app.gateway.health import HealthMonitor
    from app.gateway.reputation_cache import ReputationCache

logger = logging.getLogger(__name__)

EVAL_INTERVAL_S = 30


class AlertsEngine:
    """Evaluates alert rules against current gateway state on a periodic loop."""

    def __init__(
        self,
        health_monitor: HealthMonitor,
        reputation_cache: ReputationCache,
    ) -> None:
        self._health = health_monitor
        self._rep_cache = reputation_cache
        self._running = False

    async def start_eval_loop(self) -> None:
        self._running = True
        while self._running:
            await asyncio.sleep(EVAL_INTERVAL_S)
            try:
                await self._evaluate_all()
            except Exception:
                logger.exception("Alert evaluation cycle failed")

    def stop(self) -> None:
        self._running = False

    async def _evaluate_all(self) -> None:
        async with async_session() as session:
            result = await session.execute(
                select(AlertRule).where(AlertRule.active.is_(True))
            )
            rules = result.scalars().all()

            for rule in rules:
                agents = self._get_target_agents(rule)
                for agent_id in agents:
                    triggered = await self._check_condition(rule, agent_id, session)
                    if triggered:
                        await self._fire_alert(rule, agent_id, triggered, session)

    def _get_target_agents(self, rule: AlertRule) -> list[str]:
        if rule.agent_filter:
            stats = self._health.get_agent(rule.agent_filter)
            return [stats.agent_id] if stats else []
        return [s.agent_id for s in self._health.get_all_agents()]

    async def _check_condition(
        self, rule: AlertRule, agent_id: str, session: AsyncSession
    ) -> dict[str, Any] | None:
        ct = rule.condition_type

        if ct == AlertConditionType.REPUTATION_BELOW:
            score = await self._rep_cache.get(agent_id)
            if score is not None and score < rule.threshold:
                return {"reputation_score": score, "threshold": rule.threshold}

        elif ct == AlertConditionType.ERROR_RATE_ABOVE:
            stats = self._health.get_agent(agent_id)
            if stats and stats.error_rate > rule.threshold:
                return {"error_rate": stats.error_rate, "threshold": rule.threshold}

        elif ct == AlertConditionType.SPENDING_APPROACHING:
            # Threshold interpreted as percentage (0-1) of spending limit approached
            pass

        elif ct == AlertConditionType.ANOMALOUS_VOLUME:
            stats = self._health.get_agent(agent_id)
            if stats and stats.request_count > rule.threshold:
                return {"request_count": stats.request_count, "threshold": rule.threshold}

        elif ct == AlertConditionType.POLICY_VIOLATION_SPIKE:
            count = await session.execute(
                select(func.count(AuditEntry.id))
                .where(AuditEntry.source_agent == agent_id)
                .where(AuditEntry.policy_decision == PolicyDecisionType.BLOCK)
            )
            violations = count.scalar() or 0
            if violations > rule.threshold:
                return {"violations": violations, "threshold": rule.threshold}

        return None

    async def _fire_alert(
        self,
        rule: AlertRule,
        agent_id: str,
        details: dict[str, Any],
        session: AsyncSession,
    ) -> None:
        existing = await session.execute(
            select(AlertEvent)
            .where(AlertEvent.rule_id == rule.id)
            .where(AlertEvent.agent_id == agent_id)
            .where(AlertEvent.resolved_at.is_(None))
        )
        if existing.scalars().first():
            return

        event = AlertEvent(
            rule_id=rule.id,
            agent_id=agent_id,
            triggered_at=datetime.now(timezone.utc),
            details=details,
        )
        session.add(event)
        await session.commit()
        logger.warning(
            "Alert fired: rule=%s agent=%s details=%s", rule.name, agent_id, details
        )

        await self._notify(rule, agent_id, details)

    async def _notify(
        self, rule: AlertRule, agent_id: str, details: dict[str, Any]
    ) -> None:
        if rule.channel == AlertChannel.DASHBOARD:
            pass  # Picked up by the dashboard polling /api/gateway/alerts

        elif rule.channel == AlertChannel.WEBHOOK:
            webhook_url = details.get("webhook_url")
            if webhook_url:
                try:
                    async with httpx.AsyncClient(timeout=5.0) as client:
                        await client.post(webhook_url, json={
                            "rule": rule.name,
                            "agent_id": agent_id,
                            "details": details,
                        })
                except Exception:
                    logger.exception("Webhook notification failed for rule %s", rule.name)

        elif rule.channel == AlertChannel.EMAIL:
            logger.info(
                "Email alert (not yet configured): rule=%s agent=%s", rule.name, agent_id
            )
