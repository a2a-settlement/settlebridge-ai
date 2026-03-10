from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.models.submission import SubmissionStatus


class DeliverablePayload(BaseModel):
    content: str
    content_type: str = "text/plain"
    attachments: list[dict] | None = None
    metadata: dict | None = None


class ProvenancePayload(BaseModel):
    source_type: str = "generated"
    source_refs: list[str] | None = None
    timestamps: list[dict] | None = None
    content_hash: str | None = None
    attestation_level: str = "self_declared"


class SubmitWorkRequest(BaseModel):
    deliverable: DeliverablePayload
    provenance: ProvenancePayload | None = None


class ReviewRequest(BaseModel):
    notes: str | None = None


class ScoredApprovalRequest(BaseModel):
    score: int = Field(100, ge=0, le=100)
    release_percent: int = Field(100, ge=1, le=100)
    efficacy_check_at: datetime | None = None
    efficacy_criteria: str | None = None
    notes: str | None = None


class EfficacyReviewRequest(BaseModel):
    score: int = Field(..., ge=0, le=100)
    action: Literal["release", "refund"]
    notes: str | None = None


class DisputeRequest(BaseModel):
    reason: str


class SubmissionResponse(BaseModel):
    id: uuid.UUID
    claim_id: uuid.UUID
    bounty_id: uuid.UUID
    agent_user_id: uuid.UUID
    deliverable: dict
    provenance: dict | None = None
    status: SubmissionStatus
    reviewer_notes: str | None = None
    submitted_at: datetime
    reviewed_at: datetime | None = None
    score: int | None = None
    release_percent: int | None = None
    efficacy_check_at: datetime | None = None
    efficacy_criteria: str | None = None
    efficacy_score: int | None = None
    efficacy_reviewed_at: datetime | None = None
    ai_review: dict | None = None

    model_config = {"from_attributes": True}
