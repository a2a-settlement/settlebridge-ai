from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class PolicyDecisionType(str, enum.Enum):
    APPROVE = "approve"
    BLOCK = "block"
    FLAG = "flag"


class AlertConditionType(str, enum.Enum):
    REPUTATION_BELOW = "reputation_below"
    SPENDING_APPROACHING = "spending_approaching"
    ERROR_RATE_ABOVE = "error_rate_above"
    ANOMALOUS_VOLUME = "anomalous_volume"
    POLICY_VIOLATION_SPIKE = "policy_violation_spike"
    SELF_DEALING_RATIO_ABOVE = "self_dealing_ratio_above"
    PRINCIPAL_CLUSTER_EXPANSION = "principal_cluster_expansion"


class AlertChannel(str, enum.Enum):
    DASHBOARD = "dashboard"
    WEBHOOK = "webhook"
    EMAIL = "email"


class TrustPolicy(Base):
    __tablename__ = "trust_policies"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    yaml_content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class AuditEntry(Base):
    __tablename__ = "audit_entries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_agent: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_agent: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    policy_decision: Mapped[PolicyDecisionType] = mapped_column(
        Enum(PolicyDecisionType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    escrow_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    merkle_root: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class ReputationSnapshot(Base):
    __tablename__ = "reputation_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    bot_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reputation_score: Mapped[float] = mapped_column(Float, nullable=False)
    self_dealing_ratio_90d: Mapped[float | None] = mapped_column(Float, nullable=True)
    counterparty_hhi: Mapped[float | None] = mapped_column(Float, nullable=True)
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    condition_type: Mapped[AlertConditionType] = mapped_column(
        Enum(AlertConditionType, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
    )
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    channel: Mapped[AlertChannel] = mapped_column(
        Enum(AlertChannel, values_callable=lambda obj: [e.value for e in obj]),
        nullable=False,
        default=AlertChannel.DASHBOARD,
    )
    agent_filter: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    events: Mapped[list[AlertEvent]] = relationship(back_populates="rule")


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rule_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("alert_rules.id"), nullable=False
    )
    agent_id: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    details: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    rule: Mapped[AlertRule] = relationship(back_populates="events")


class GatewayAgent(Base):
    """Local record of agents claimed by this gateway instance."""

    __tablename__ = "gateway_agents"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    exchange_account_id: Mapped[str] = mapped_column(String(36), nullable=False, unique=True, index=True)
    bot_name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    skills: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    exchange_claim_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="active")
    principal_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True)
