from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.gateway import AlertChannel, AlertConditionType, PolicyDecisionType


# --- Trust Policy ---


class TrustPolicyCreate(BaseModel):
    name: str
    yaml_content: str


class TrustPolicyUpdate(BaseModel):
    name: str | None = None
    yaml_content: str | None = None
    active: bool | None = None


class TrustPolicyResponse(BaseModel):
    id: uuid.UUID
    name: str
    yaml_content: str
    version: int
    active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class PolicyValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    matched_transactions: int = 0
    would_block: int = 0
    would_flag: int = 0


# --- Audit ---


class AuditEntryResponse(BaseModel):
    id: uuid.UUID
    timestamp: datetime
    request_hash: str
    source_agent: str
    target_agent: str
    policy_decision: PolicyDecisionType
    escrow_id: str | None = None
    latency_ms: int | None = None
    response_status: int | None = None
    merkle_root: str | None = None
    details: dict | None = None

    model_config = {"from_attributes": True}


class AuditListResponse(BaseModel):
    entries: list[AuditEntryResponse]
    total: int
    page: int
    page_size: int


# --- Reputation ---


class ReputationSnapshotResponse(BaseModel):
    id: uuid.UUID
    agent_id: str
    bot_id: str
    reputation_score: float
    snapshot_at: datetime

    model_config = {"from_attributes": True}


# --- Agent Health ---


class AgentHealthResponse(BaseModel):
    agent_id: str
    bot_id: str
    status: str
    reputation_score: float | None = None
    avg_latency_ms: float | None = None
    error_rate: float | None = None
    request_count: int = 0
    last_seen: datetime | None = None


class AgentDetailResponse(AgentHealthResponse):
    reputation_history: list[ReputationSnapshotResponse] = Field(default_factory=list)
    recent_transactions: list[AuditEntryResponse] = Field(default_factory=list)


# --- Alerts ---


class AlertRuleCreate(BaseModel):
    name: str
    condition_type: AlertConditionType
    threshold: float
    channel: AlertChannel = AlertChannel.DASHBOARD
    agent_filter: str | None = None


class AlertRuleUpdate(BaseModel):
    name: str | None = None
    condition_type: AlertConditionType | None = None
    threshold: float | None = None
    channel: AlertChannel | None = None
    agent_filter: str | None = None
    active: bool | None = None


class AlertRuleResponse(BaseModel):
    id: uuid.UUID
    name: str
    condition_type: AlertConditionType
    threshold: float
    channel: AlertChannel
    agent_filter: str | None = None
    active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class AlertEventResponse(BaseModel):
    id: uuid.UUID
    rule_id: uuid.UUID
    agent_id: str
    triggered_at: datetime
    resolved_at: datetime | None = None
    details: dict | None = None

    model_config = {"from_attributes": True}


class AlertListResponse(BaseModel):
    alerts: list[AlertEventResponse]
    rules: list[AlertRuleResponse]


# --- Gateway Overview ---


class GatewayHealthResponse(BaseModel):
    status: str
    uptime_seconds: float
    active_agents: int
    total_transactions: int
    policy_violations_24h: int
    avg_latency_ms: float
    exchange_connected: bool


class SettlementOverviewResponse(BaseModel):
    active_escrows: int
    total_locked: int
    total_released: int
    total_disputed: int
    treasury_fees: int
    top_agents: list[dict] = Field(default_factory=list)


class GatewayMetricsResponse(BaseModel):
    transactions_per_hour: float
    requests_by_decision: dict[str, int] = Field(default_factory=dict)
    error_rate: float
    avg_latency_ms: float
    cache_hit_rate: float
