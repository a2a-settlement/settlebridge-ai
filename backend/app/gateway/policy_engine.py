"""Trust Policy Engine -- YAML-based policy evaluation with hot-reload."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session
from app.models.gateway import PolicyDecisionType, TrustPolicy

logger = logging.getLogger(__name__)


class Action(str, Enum):
    APPROVE = "approve"
    BLOCK = "block"
    FLAG = "flag"


@dataclass
class PolicyDecision:
    action: Action
    reasons: list[str] = field(default_factory=list)
    matched_policies: list[str] = field(default_factory=list)

    @property
    def db_decision(self) -> PolicyDecisionType:
        return PolicyDecisionType(self.action.value)


@dataclass
class AttestationFreshness:
    """Attestation lifecycle metadata resolved from the exchange."""

    identity_verified_days_ago: int | None = None
    identity_status: str = "unknown"
    capability_status: str = "unknown"
    attestation_valid: bool = False


@dataclass
class GatewayRequest:
    """Normalised request context for policy evaluation."""

    source_agent: str
    target_agent: str
    escrow_id: str | None = None
    escrow_amount: float = 0.0
    reputation_score: float | None = None
    attestation_level: str | None = None
    attestation_freshness: AttestationFreshness | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ParsedRule:
    field: str
    value: Any


@dataclass
class ParsedPolicy:
    name: str
    match: dict[str, Any]
    rules: list[ParsedRule]


class PolicyEngine:
    """Evaluates trust policies against gateway requests.

    Policies are loaded from the DB and cached in memory. A background task
    reloads them every ``POLICY_RELOAD_INTERVAL_S`` seconds.
    """

    def __init__(self) -> None:
        self._policies: list[ParsedPolicy] = []
        self._rate_counters: dict[str, list[float]] = {}
        self._running = False

    # ---- loading / hot-reload ------------------------------------------------

    def load_from_yaml(self, raw: str) -> list[ParsedPolicy]:
        doc = yaml.safe_load(raw)
        if not doc or "policies" not in doc:
            return []
        out: list[ParsedPolicy] = []
        for p in doc["policies"]:
            rules = []
            for r in p.get("rules", []):
                for k, v in r.items():
                    rules.append(ParsedRule(field=k, value=v))
            out.append(ParsedPolicy(name=p["name"], match=p.get("match", {}), rules=rules))
        return out

    async def reload_from_db(self) -> None:
        async with async_session() as session:
            result = await session.execute(
                select(TrustPolicy).where(TrustPolicy.active.is_(True))
            )
            rows = result.scalars().all()

        merged: list[ParsedPolicy] = []
        for row in rows:
            try:
                merged.extend(self.load_from_yaml(row.yaml_content))
            except Exception:
                logger.exception("Failed to parse policy %s", row.name)
        self._policies = merged
        logger.info("Loaded %d policies from %d DB rows", len(merged), len(rows))

    async def start_reload_loop(self) -> None:
        self._running = True
        await self.reload_from_db()
        while self._running:
            await asyncio.sleep(settings.POLICY_RELOAD_INTERVAL_S)
            try:
                await self.reload_from_db()
            except Exception:
                logger.exception("Policy reload failed")

    def stop(self) -> None:
        self._running = False

    # ---- validation ----------------------------------------------------------

    @staticmethod
    def validate_yaml(raw: str) -> tuple[bool, list[str]]:
        errors: list[str] = []
        try:
            doc = yaml.safe_load(raw)
        except yaml.YAMLError as exc:
            return False, [str(exc)]
        if not isinstance(doc, dict):
            return False, ["Root must be a mapping"]
        if "policies" not in doc:
            return False, ["Missing top-level 'policies' key"]
        for i, p in enumerate(doc["policies"]):
            if "name" not in p:
                errors.append(f"Policy {i}: missing 'name'")
            if "rules" not in p:
                errors.append(f"Policy {i}: missing 'rules'")
        return len(errors) == 0, errors

    # ---- evaluation ----------------------------------------------------------

    def _matches(self, policy: ParsedPolicy, req: GatewayRequest) -> bool:
        m = policy.match
        if m.get("all_agents"):
            return True
        if "source_agent" in m and m["source_agent"] != req.source_agent:
            return False
        if "target_agent" in m and m["target_agent"] != req.target_agent:
            return False
        if "escrow_amount_gte" in m and req.escrow_amount < m["escrow_amount_gte"]:
            return False
        return True

    def _evaluate_rule(self, rule: ParsedRule, req: GatewayRequest) -> str | None:
        """Return a reason string if the rule is violated, else None."""
        f, v = rule.field, rule.value

        if f == "reputation_gte":
            if req.reputation_score is not None and req.reputation_score < v:
                return f"Reputation {req.reputation_score:.2f} < minimum {v}"

        elif f == "required_attestation":
            if req.attestation_level and req.attestation_level != v:
                return f"Attestation '{req.attestation_level}' != required '{v}'"

        elif f == "max_requests_per_minute":
            key = req.source_agent
            now = time.time()
            window = self._rate_counters.setdefault(key, [])
            window[:] = [t for t in window if now - t < 60]
            window.append(now)
            if len(window) > v:
                return f"Rate limit exceeded: {len(window)}/{v} per minute"

        elif f == "max_escrow_amount":
            if req.escrow_amount > v:
                return f"Escrow amount {req.escrow_amount} > limit {v}"

        elif f == "require_counterparty_allowlist":
            allowed = req.metadata.get("counterparty_allowed", True)
            if v and not allowed:
                return "Counterparty not on allowlist"

        elif f == "require_valid_attestation":
            if v and req.attestation_freshness:
                if not req.attestation_freshness.attestation_valid:
                    return (
                        f"Agent attestation invalid "
                        f"(identity={req.attestation_freshness.identity_status}, "
                        f"capability={req.attestation_freshness.capability_status})"
                    )
            elif v and not req.attestation_freshness:
                return "No attestation freshness data available"

        elif f == "max_identity_age_days":
            if req.attestation_freshness and req.attestation_freshness.identity_verified_days_ago is not None:
                if req.attestation_freshness.identity_verified_days_ago > v:
                    return (
                        f"Identity verified {req.attestation_freshness.identity_verified_days_ago}d ago "
                        f"> maximum {v}d"
                    )

        return None

    def evaluate(self, req: GatewayRequest) -> PolicyDecision:
        if not self._policies:
            return PolicyDecision(action=Action.APPROVE, reasons=["No policies configured"])

        all_reasons: list[str] = []
        matched_names: list[str] = []
        block = False

        for policy in self._policies:
            if not self._matches(policy, req):
                continue
            matched_names.append(policy.name)
            for rule in policy.rules:
                reason = self._evaluate_rule(rule, req)
                if reason:
                    all_reasons.append(f"[{policy.name}] {reason}")
                    block = True

        if block:
            return PolicyDecision(
                action=Action.BLOCK, reasons=all_reasons, matched_policies=matched_names
            )
        if all_reasons:
            return PolicyDecision(
                action=Action.FLAG, reasons=all_reasons, matched_policies=matched_names
            )
        return PolicyDecision(
            action=Action.APPROVE,
            reasons=["All policies passed"],
            matched_policies=matched_names,
        )

    async def dry_run(self, yaml_content: str, session: AsyncSession) -> dict:
        """Evaluate a candidate policy against recent audit entries."""
        from app.models.gateway import AuditEntry

        test_policies = self.load_from_yaml(yaml_content)
        result = await session.execute(
            select(AuditEntry).order_by(AuditEntry.timestamp.desc()).limit(100)
        )
        entries = result.scalars().all()

        would_block = 0
        would_flag = 0
        for entry in entries:
            req = GatewayRequest(
                source_agent=entry.source_agent,
                target_agent=entry.target_agent,
                escrow_id=entry.escrow_id,
            )
            for p in test_policies:
                if self._matches(p, req):
                    for rule in p.rules:
                        if self._evaluate_rule(rule, req):
                            would_block += 1
                            break

        return {
            "matched_transactions": len(entries),
            "would_block": would_block,
            "would_flag": would_flag,
        }
