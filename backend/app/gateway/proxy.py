"""Gateway proxy -- subclasses ShimProxy to add policy enforcement, reputation, and audit."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from opentelemetry import trace

from shim.models import ProxyRequest, ProxyResponse
from shim.proxy import ShimProxy

from app.database import async_session
from app.gateway.policy_engine import Action, AttestationFreshness, GatewayRequest, PolicyEngine
from app.models.gateway import PolicyDecisionType

if TYPE_CHECKING:
    from app.gateway.audit import AuditLogger
    from app.gateway.health import HealthMonitor
    from app.gateway.reputation_cache import ReputationCache

logger = logging.getLogger(__name__)
tracer = trace.get_tracer("settlebridge.gateway")


class GatewayProxy(ShimProxy):
    """Extends ShimProxy with trust policy enforcement, reputation checks, and audit logging.

    Pipeline: receive -> policy check -> reputation lookup -> [parent: escrow gate ->
    credential inject -> forward] -> audit log -> return
    """

    def __init__(
        self,
        *args,
        policy_engine: PolicyEngine,
        reputation_cache: ReputationCache,
        audit_logger: AuditLogger,
        health_monitor: HealthMonitor,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._policy_engine = policy_engine
        self._rep_cache = reputation_cache
        self._audit_logger = audit_logger
        self._health_monitor = health_monitor

    async def handle(self, request: ProxyRequest) -> ProxyResponse:
        with tracer.start_as_current_span("gateway.handle") as span:
            span.set_attribute("gateway.source_agent", request.agent_id or "unknown")
            span.set_attribute("gateway.escrow_id", request.escrow_id or "")

            source = request.agent_id or "unknown"
            target = request.destination_url or request.tool_id or "unknown"
            start = time.monotonic()

            # Step 1: Reputation lookup
            rep_score = await self._rep_cache.get(source)
            span.set_attribute("gateway.reputation_score", rep_score or -1)

            # Step 1b: Attestation freshness lookup
            att_freshness: AttestationFreshness | None = None
            raw_freshness = await self._rep_cache.get_attestation_freshness(source)
            if raw_freshness:
                att_freshness = AttestationFreshness(
                    identity_verified_days_ago=raw_freshness.get("identity_verified_days_ago"),
                    identity_status=raw_freshness.get("identity_status", "unknown"),
                    capability_status=raw_freshness.get("capability_status", "unknown"),
                    attestation_valid=raw_freshness.get("attestation_valid", False),
                )
                span.set_attribute("gateway.attestation_valid", att_freshness.attestation_valid)

            # Step 2: Policy check
            gw_req = GatewayRequest(
                source_agent=source,
                target_agent=target,
                escrow_id=request.escrow_id,
                reputation_score=rep_score,
                attestation_freshness=att_freshness,
            )
            decision = self._policy_engine.evaluate(gw_req)
            span.set_attribute("gateway.policy_decision", decision.action.value)

            if decision.action == Action.BLOCK:
                latency_ms = int((time.monotonic() - start) * 1000)
                self._health_monitor.record_request(source, latency_ms, is_error=True)
                await self._write_audit(
                    source, target, PolicyDecisionType.BLOCK,
                    request.escrow_id, latency_ms, 403,
                    {"reasons": decision.reasons},
                )
                import json
                return ProxyResponse(
                    status_code=403,
                    body=json.dumps({
                        "error": "Request blocked by trust policy",
                        "reasons": decision.reasons,
                    }),
                )

            # Step 3: Delegate to parent pipeline (escrow gate -> inject -> forward)
            response = await super().handle(request)
            latency_ms = int((time.monotonic() - start) * 1000)

            # Step 4: Record health
            is_error = response.status_code >= 500
            self._health_monitor.record_request(source, latency_ms, is_error=is_error)

            # Step 5: Audit log
            db_decision = (
                PolicyDecisionType.FLAG
                if decision.action == Action.FLAG
                else PolicyDecisionType.APPROVE
            )
            await self._write_audit(
                source, target, db_decision,
                request.escrow_id, latency_ms, response.status_code,
                {"reasons": decision.reasons} if decision.reasons else None,
            )

            span.set_attribute("gateway.latency_ms", latency_ms)
            span.set_attribute("gateway.response_status", response.status_code)
            return response

    async def _write_audit(
        self,
        source: str,
        target: str,
        decision: PolicyDecisionType,
        escrow_id: str | None,
        latency_ms: int,
        status_code: int,
        details: dict | None,
    ) -> None:
        try:
            async with async_session() as session:
                await self._audit_logger.log(
                    session,
                    source_agent=source,
                    target_agent=target,
                    policy_decision=decision,
                    escrow_id=escrow_id,
                    latency_ms=latency_ms,
                    response_status=status_code,
                    details=details,
                )
        except Exception:
            logger.exception("Failed to write audit entry")
