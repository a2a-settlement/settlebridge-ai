from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.assist_session import AssistSessionStatus
from app.models.bounty import Difficulty, ProvenanceTier


class StartSessionRequest(BaseModel):
    initial_message: str = Field(..., min_length=1, max_length=5000)


class SendMessageRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=5000)


class FinalizeSessionRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    category_id: uuid.UUID | None = None
    tags: list[str] | None = None
    reward_amount: int | None = None
    difficulty: Difficulty | None = None
    provenance_tier: ProvenanceTier | None = None
    deadline: datetime | None = None


class ReputationStake(BaseModel):
    enabled: bool = True
    weight: float = 1.0


class PerformanceTranche(BaseModel):
    percent: int = Field(..., ge=1, le=100)
    indicator: str
    measurement: str = "observable_market_data"
    escrow_duration_days: int = Field(90, ge=1, le=730)
    partial_credit: bool = True


class SettlementStructure(BaseModel):
    immediate_payout_percent: int = Field(100, ge=0, le=100)
    performance_tranches: list[PerformanceTranche] | None = None
    reputation_stake: ReputationStake | None = None


class AcceptanceCriteriaAssist(BaseModel):
    description: str = ""
    output_format: str = ""
    required_sources: list[str] | None = None
    provenance_tier: str = "tier1_self_declared"
    custom_checks: list[dict] | None = None


class BountyDraft(BaseModel):
    title: str | None = None
    description: str | None = None
    category_slug: str | None = None
    tags: list[str] | None = None
    acceptance_criteria: AcceptanceCriteriaAssist | None = None
    reward_suggestion: int | None = None
    difficulty: str | None = None
    provenance_tier: str | None = None
    deadline_suggestion: str | None = None
    settlement_structure: SettlementStructure | None = None


class MessageResponse(BaseModel):
    role: str
    content: str
    timestamp: datetime


class AssistSessionResponse(BaseModel):
    id: uuid.UUID
    status: AssistSessionStatus
    messages: list[dict]
    bounty_draft: BountyDraft | None = None
    settlement_structure: SettlementStructure | None = None
    turn_count: int
    created_at: datetime
    updated_at: datetime
    finalized_bounty_id: uuid.UUID | None = None

    model_config = {"from_attributes": True}


class AssistSessionListResponse(BaseModel):
    sessions: list[AssistSessionResponse]
    total: int
