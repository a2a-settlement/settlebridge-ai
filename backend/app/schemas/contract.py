from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.bounty import ProvenanceTier
from app.models.contract import ContractStatus
from app.models.snapshot import SnapshotStatus


class AcceptanceCriteria(BaseModel):
    description: str = ""
    output_format: str = ""
    required_sources: list[str] | None = None
    custom_checks: list[dict] | None = None


class ContractCreateRequest(BaseModel):
    title: str
    description: str
    agent_user_id: uuid.UUID
    agent_exchange_bot_id: str
    category_id: uuid.UUID | None = None
    acceptance_criteria: AcceptanceCriteria | None = None
    provenance_tier: ProvenanceTier = ProvenanceTier.TIER1_SELF_DECLARED
    reward_per_snapshot: int
    schedule: str
    schedule_description: str
    max_snapshots: int | None = None
    grace_period_hours: int = 24
    auto_approve: bool = True


class ContractResponse(BaseModel):
    id: uuid.UUID
    requester_id: uuid.UUID
    agent_user_id: uuid.UUID
    agent_exchange_bot_id: str
    title: str
    description: str
    category_id: uuid.UUID | None = None
    acceptance_criteria: dict | None = None
    provenance_tier: ProvenanceTier
    reward_per_snapshot: int
    schedule: str
    schedule_description: str
    max_snapshots: int | None = None
    grace_period_hours: int
    auto_approve: bool
    status: ContractStatus
    group_id: str
    created_at: datetime
    updated_at: datetime
    activated_at: datetime | None = None
    cancelled_at: datetime | None = None
    snapshot_count: int = 0

    model_config = {"from_attributes": True}


class ContractListResponse(BaseModel):
    contracts: list[ContractResponse]
    total: int


class SnapshotDeliverRequest(BaseModel):
    content: str
    content_type: str = "text/plain"
    attachments: list[dict] | None = None
    metadata: dict | None = None
    provenance: dict | None = None


class SnapshotResponse(BaseModel):
    id: uuid.UUID
    contract_id: uuid.UUID
    cycle_number: int
    escrow_id: str | None = None
    deliverable: dict | None = None
    provenance: dict | None = None
    status: SnapshotStatus
    due_at: datetime
    deadline_at: datetime
    delivered_at: datetime | None = None
    approved_at: datetime | None = None
    reviewer_notes: str | None = None

    model_config = {"from_attributes": True}


class SnapshotListResponse(BaseModel):
    snapshots: list[SnapshotResponse]
    total: int


class DisputeRequest(BaseModel):
    reason: str


class ReviewRequest(BaseModel):
    notes: str | None = None
