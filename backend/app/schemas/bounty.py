from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.bounty import BountyStatus, Difficulty, ProvenanceTier


class AcceptanceCriteria(BaseModel):
    description: str = ""
    output_format: str = ""
    required_sources: list[str] | None = None
    provenance_tier: ProvenanceTier = ProvenanceTier.TIER1_SELF_DECLARED
    custom_checks: list[dict] | None = None


class BountyCreateRequest(BaseModel):
    title: str
    description: str
    category_id: uuid.UUID | None = None
    tags: list[str] | None = None
    acceptance_criteria: AcceptanceCriteria | None = None
    reward_amount: int
    deadline: datetime | None = None
    max_claims: int = 1
    min_reputation: float | None = None
    difficulty: Difficulty = Difficulty.MEDIUM
    auto_approve: bool = False
    provenance_tier: ProvenanceTier = ProvenanceTier.TIER1_SELF_DECLARED
    settlement_structure: dict | None = None


class BountyUpdateRequest(BaseModel):
    title: str | None = None
    description: str | None = None
    category_id: uuid.UUID | None = None
    tags: list[str] | None = None
    acceptance_criteria: AcceptanceCriteria | None = None
    reward_amount: int | None = None
    deadline: datetime | None = None
    max_claims: int | None = None
    min_reputation: float | None = None
    difficulty: Difficulty | None = None
    auto_approve: bool | None = None
    provenance_tier: ProvenanceTier | None = None
    settlement_structure: dict | None = None


class CategoryResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str
    icon: str | None = None
    sort_order: int

    model_config = {"from_attributes": True}


class BountyResponse(BaseModel):
    id: uuid.UUID
    requester_id: uuid.UUID
    title: str
    description: str
    category_id: uuid.UUID | None = None
    category: CategoryResponse | None = None
    tags: list[str] | None = None
    acceptance_criteria: dict | None = None
    reward_amount: int
    escrow_id: str | None = None
    status: BountyStatus
    deadline: datetime | None = None
    max_claims: int
    min_reputation: float | None = None
    difficulty: Difficulty
    auto_approve: bool
    provenance_tier: ProvenanceTier
    settlement_structure: dict | None = None
    active_claims_count: int = 0
    created_at: datetime
    updated_at: datetime
    funded_at: datetime | None = None
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class BountyListResponse(BaseModel):
    bounties: list[BountyResponse]
    total: int
    page: int
    page_size: int
